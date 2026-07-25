#!/usr/bin/env python3
"""Create and compare protected-profile fingerprints for Phase 8 evidence.

All files are byte-hashed except ``skills/.usage.json``. That file is also
raw-hashed for audit, but its gating digest is a canonical projection of the
policy-bearing fields approved during Layer-4 review. Missing, unreadable, or
malformed usage data fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

POLICY_FIELDS = (
    "state",
    "pinned",
    "created_by",
    "agent_created",
    "archived_at",
    "created_at",
)
VOLATILE_FIELDS = (
    "use_count",
    "view_count",
    "patch_count",
    "last_used_at",
    "last_viewed_at",
    "last_patched_at",
)
KNOWN_USAGE_FIELDS = frozenset(POLICY_FIELDS + VOLATILE_FIELDS)
USAGE_PATH = "skills/.usage.json"
FINGERPRINT_SCHEMA = "phase8-protected-profiles/v2"


class FingerprintError(RuntimeError):
    """Raised when a protected profile cannot be fingerprinted safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_usage(raw: bytes) -> tuple[str, int]:
    """Return the canonical policy digest and number of skill records."""
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FingerprintError(f"malformed {USAGE_PATH}: {type(exc).__name__}") from exc
    if not isinstance(parsed, dict):
        raise FingerprintError(f"malformed {USAGE_PATH}: top-level value is not an object")

    projection: dict[str, dict[str, Any]] = {}
    for skill_name in sorted(parsed):
        record = parsed[skill_name]
        if not isinstance(skill_name, str) or not isinstance(record, dict):
            raise FingerprintError(f"malformed {USAGE_PATH}: invalid skill record")
        unknown = sorted(set(record) - KNOWN_USAGE_FIELDS)
        if unknown:
            raise FingerprintError(
                f"malformed {USAGE_PATH}: undocumented fields for {skill_name}: {','.join(unknown)}"
            )
        projection[skill_name] = {field: record.get(field) for field in POLICY_FIELDS}

    canonical = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256(canonical), len(projection)


def _entry(path: Path, root: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FingerprintError(f"cannot stat {path}: {type(exc).__name__}") from exc
    relative = path.relative_to(root).as_posix()
    if stat.S_ISLNK(info.st_mode):
        return {"path": relative, "type": "symlink", "target": os.readlink(path)}
    if not stat.S_ISREG(info.st_mode):
        raise FingerprintError(f"unsupported protected path type: {relative}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FingerprintError(f"cannot read {relative}: {type(exc).__name__}") from exc
    entry: dict[str, Any] = {
        "path": relative,
        "type": "file",
        "raw_sha256": _sha256(raw),
        "size": len(raw),
    }
    if relative == USAGE_PATH:
        normalized_sha256, record_count = normalize_usage(raw)
        entry.update(
            normalized_sha256=normalized_sha256,
            normalized_record_count=record_count,
            gating_digest="normalized_sha256",
        )
    else:
        entry.update(gating_digest="raw_sha256")
    return entry


def fingerprint_profile(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise FingerprintError(f"profile root is missing: {root}")
    usage = root / USAGE_PATH
    if not usage.is_file():
        raise FingerprintError(f"required protected file is missing: {usage}")

    rows: list[dict[str, Any]] = []
    for start in (root / "config.yaml", root / "skills", root / "memories"):
        if start.is_symlink() or start.is_file():
            rows.append(_entry(start, root))
        elif start.is_dir():
            for directory, dirs, files in os.walk(start, followlinks=False):
                directory_path = Path(directory)
                links = []
                for dirname in list(dirs):
                    candidate = directory_path / dirname
                    if candidate.is_symlink():
                        links.append(candidate)
                        dirs.remove(dirname)
                for candidate in links + [directory_path / name for name in files]:
                    rows.append(_entry(candidate, root))
        elif start == root / "config.yaml":
            raise FingerprintError(f"required protected file is missing: {start}")
    rows.sort(key=lambda row: row["path"])
    gating_rows = [
        {
            "path": row["path"],
            "type": row["type"],
            "digest": row.get(row.get("gating_digest", "")),
            **({"target": row["target"]} if row["type"] == "symlink" else {}),
        }
        for row in rows
    ]
    return {
        "entry_count": len(rows),
        "raw_aggregate_sha256": _sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "normalized_policy_aggregate_sha256": _sha256(
            json.dumps(gating_rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "entries": rows,
    }


def profile_root(hermes_home: Path, name: str) -> Path:
    return hermes_home if name == "default" else hermes_home / "profiles" / name


def snapshot(hermes_home: Path, profiles: list[str], output: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "schema": FINGERPRINT_SCHEMA,
        "normalization": {
            "path": USAGE_PATH,
            "policy_fields_retained": list(POLICY_FIELDS),
            "volatile_fields_ignored_for_gating_only": list(VOLATILE_FIELDS),
            "raw_hash_recorded": True,
            "unknown_fields": "fail_closed",
            "missing_or_malformed": "fail_closed",
        },
        "profiles": {},
        "errors": [],
    }
    for name in sorted(profiles):
        try:
            evidence["profiles"][name] = fingerprint_profile(profile_root(hermes_home, name))
        except FingerprintError as exc:
            evidence["errors"].append({"profile": name, "error": str(exc)})
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence


def _gate_failure(reason: str, **details: Any) -> tuple[bool, list[dict[str, Any]]]:
    return False, [{"profile": "<gate>", "status": "FAIL", "reason": reason, **details}]


def compare(
    before: Any,
    after: Any,
    *,
    expected_profiles: list[str],
) -> tuple[bool, list[dict[str, Any]]]:
    """Compare matching v2 fingerprints against an explicit profile contract."""
    if not expected_profiles:
        return _gate_failure("expected profile set is empty")
    if any(not isinstance(name, str) or not name for name in expected_profiles):
        return _gate_failure("expected profile set is malformed")
    expected = set(expected_profiles)
    if len(expected) != len(expected_profiles):
        return _gate_failure("expected profile set contains duplicates")

    if not isinstance(before, dict) or not isinstance(after, dict):
        return _gate_failure("fingerprint document is not an object")
    if before.get("schema") != FINGERPRINT_SCHEMA or after.get("schema") != FINGERPRINT_SCHEMA:
        return _gate_failure(
            "fingerprint schema mismatch",
            expected_schema=FINGERPRINT_SCHEMA,
            before_schema=before.get("schema"),
            after_schema=after.get("schema"),
        )

    before_profiles = before.get("profiles")
    after_profiles = after.get("profiles")
    before_errors = before.get("errors")
    after_errors = after.get("errors")
    if not isinstance(before_profiles, dict) or not isinstance(after_profiles, dict):
        return _gate_failure("fingerprint profiles value is not an object")
    if not isinstance(before_errors, list) or not isinstance(after_errors, list):
        return _gate_failure("fingerprint errors value is not an array")
    if any(not isinstance(name, str) or not name for name in set(before_profiles) | set(after_profiles)):
        return _gate_failure("fingerprint profile name is malformed")

    results: list[dict[str, Any]] = []
    before_names = set(before_profiles)
    after_names = set(after_profiles)
    for name in sorted((before_names | after_names) - expected):
        results.append(
            {
                "profile": name,
                "status": "FAIL",
                "reason": "unexpected profile in fingerprint",
                "raw_changed": False,
            }
        )
    for name in sorted(expected - (before_names & after_names)):
        results.append(
            {
                "profile": name,
                "status": "FAIL",
                "reason": "expected profile missing from fingerprint",
                "raw_changed": False,
            }
        )

    required_digests = ("normalized_policy_aggregate_sha256", "raw_aggregate_sha256")
    for name in sorted(expected & before_names & after_names):
        old = before_profiles[name]
        new = after_profiles[name]
        if not isinstance(old, dict) or not isinstance(new, dict) or any(
            not isinstance(row.get(field), str)
            for row in (old, new)
            for field in required_digests
        ):
            results.append(
                {
                    "profile": name,
                    "status": "FAIL",
                    "reason": "profile fingerprint is malformed",
                    "raw_changed": False,
                }
            )
            continue
        status = "PASS"
        reason = "normalized policy aggregate unchanged"
        if old["normalized_policy_aggregate_sha256"] != new["normalized_policy_aggregate_sha256"]:
            status, reason = "FAIL", "policy-bearing or byte-exact protected data changed"
        results.append(
            {
                "profile": name,
                "status": status,
                "reason": reason,
                "raw_changed": old["raw_aggregate_sha256"] != new["raw_aggregate_sha256"],
            }
        )

    errors = before_errors + after_errors
    if errors:
        results.append(
            {
                "profile": "<gate>",
                "status": "FAIL",
                "reason": "fingerprint errors",
                "errors": errors,
            }
        )
    return bool(results) and all(result["status"] == "PASS" for result in results), results


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snap = subparsers.add_parser("snapshot")
    snap.add_argument("--hermes-home", type=Path, required=True)
    snap.add_argument("--profiles", nargs="+", required=True)
    snap.add_argument("--output", type=Path, required=True)
    check = subparsers.add_parser("compare")
    check.add_argument("--before", type=Path, required=True)
    check.add_argument("--after", type=Path, required=True)
    check.add_argument("--profiles", nargs="+", required=True)
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "snapshot":
        evidence = snapshot(args.hermes_home, args.profiles, args.output)
        print(f"SNAPSHOT={args.output}")
        print(f"SNAPSHOT_SHA256={_sha256(args.output.read_bytes())}")
        if evidence["errors"]:
            print("PROTECTED_PROFILE_SNAPSHOT=FAIL")
            return 1
        print("PROTECTED_PROFILE_SNAPSHOT=PASS")
        return 0

    try:
        before = json.loads(args.before.read_text(encoding="utf-8"))
        after = json.loads(args.after.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"PROTECTED_PROFILE_GATE=FAIL ({type(exc).__name__})")
        return 1
    passed, results = compare(before, after, expected_profiles=args.profiles)
    report = {
        "schema": "phase8-protected-profile-comparison/v2",
        "before": str(args.before),
        "after": str(args.after),
        "historical_raw_fail_reclassified": False,
        "results": results,
        "gate": "PASS" if passed else "FAIL",
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for result in results:
        print(f"{result['profile']}\t{result['status']}\t{result['reason']}\traw_changed={result.get('raw_changed', False)}")
    print(f"PROTECTED_PROFILE_GATE={report['gate']}")
    print(f"COMPARISON={args.output}")
    print(f"COMPARISON_SHA256={_sha256(args.output.read_bytes())}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
