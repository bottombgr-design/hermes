from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase8_protected_profile_gate.py"
SPEC = importlib.util.spec_from_file_location("phase8_protected_profile_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SCHEMA = "phase8-protected-profiles/v2"


def usage_record(**overrides):
    record = {
        "state": "active",
        "pinned": True,
        "created_by": "agent",
        "agent_created": True,
        "archived_at": None,
        "created_at": "2026-01-01T00:00:00Z",
        "use_count": 1,
        "view_count": 2,
        "patch_count": 3,
        "last_used_at": "2026-01-02T00:00:00Z",
        "last_viewed_at": "2026-01-03T00:00:00Z",
        "last_patched_at": "2026-01-04T00:00:00Z",
    }
    record.update(overrides)
    return record


def fingerprint(*, normalized="policy-a", raw="raw-a"):
    return {
        "normalized_policy_aggregate_sha256": normalized,
        "raw_aggregate_sha256": raw,
    }


def evidence(profiles, *, schema=SCHEMA):
    return {"schema": schema, "profiles": profiles, "errors": []}


def test_usage_normalization_ignores_only_documented_volatile_fields():
    before = json.dumps({"skill-a": usage_record()}).encode()
    after = json.dumps(
        {
            "skill-a": usage_record(
                use_count=99,
                view_count=88,
                patch_count=77,
                last_used_at="2026-07-25T00:00:00Z",
                last_viewed_at="2026-07-25T00:00:01Z",
                last_patched_at="2026-07-25T00:00:02Z",
            )
        }
    ).encode()

    assert MODULE.normalize_usage(before)[0] == MODULE.normalize_usage(after)[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "archived"),
        ("pinned", False),
        ("created_by", "human"),
        ("agent_created", False),
        ("archived_at", "2026-07-25T00:00:00Z"),
        ("created_at", "2026-02-01T00:00:00Z"),
    ],
)
def test_usage_normalization_retains_every_policy_field(field, value):
    before = json.dumps({"skill-a": usage_record()}).encode()
    after = json.dumps({"skill-a": usage_record(**{field: value})}).encode()

    assert MODULE.normalize_usage(before)[0] != MODULE.normalize_usage(after)[0]


def test_usage_normalization_retains_skill_key_set():
    before = json.dumps({"skill-a": usage_record()}).encode()
    after = json.dumps({"skill-a": usage_record(), "skill-b": usage_record()}).encode()

    assert MODULE.normalize_usage(before)[0] != MODULE.normalize_usage(after)[0]


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[]", json.dumps({"skill-a": []}).encode(), json.dumps({"skill-a": {"new_field": 1}}).encode()],
)
def test_usage_normalization_fails_closed_on_malformed_or_unknown_data(raw):
    with pytest.raises(MODULE.FingerprintError):
        MODULE.normalize_usage(raw)


def test_profile_fingerprint_fails_closed_when_usage_file_is_missing(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "memories").mkdir()
    (tmp_path / "config.yaml").write_text("model: {}\n", encoding="utf-8")

    with pytest.raises(MODULE.FingerprintError, match="required protected file is missing"):
        MODULE.fingerprint_profile(tmp_path)


def test_compare_fails_when_expected_profile_set_is_empty():
    passed, results = MODULE.compare(evidence({}), evidence({}), expected_profiles=[])

    assert passed is False
    assert results == [
        {
            "profile": "<gate>",
            "status": "FAIL",
            "reason": "expected profile set is empty",
        }
    ]


@pytest.mark.parametrize("foreign_schema", ["phase8-protected-profiles/v1", "foreign-schema"])
def test_compare_fails_on_schema_mismatch_or_foreign_schema(foreign_schema):
    passed, results = MODULE.compare(
        evidence({"default": fingerprint()}, schema=foreign_schema),
        evidence({"default": fingerprint()}),
        expected_profiles=["default"],
    )

    assert passed is False
    assert results[0]["profile"] == "<gate>"
    assert results[0]["status"] == "FAIL"
    assert results[0]["reason"] == "fingerprint schema mismatch"


def test_compare_fails_when_profile_is_added():
    passed, results = MODULE.compare(
        evidence({"default": fingerprint()}),
        evidence({"default": fingerprint(), "unexpected": fingerprint()}),
        expected_profiles=["default"],
    )

    assert passed is False
    assert next(row for row in results if row["profile"] == "unexpected")["reason"] == (
        "unexpected profile in fingerprint"
    )


def test_compare_fails_when_profile_is_removed():
    passed, results = MODULE.compare(
        evidence({"default": fingerprint()}),
        evidence({}),
        expected_profiles=["default"],
    )

    assert passed is False
    assert next(row for row in results if row["profile"] == "default")["reason"] == (
        "expected profile missing from fingerprint"
    )


def test_compare_fails_when_policy_field_digest_changes():
    passed, results = MODULE.compare(
        evidence({"default": fingerprint(normalized="policy-before")}),
        evidence({"default": fingerprint(normalized="policy-after")}),
        expected_profiles=["default"],
    )

    assert passed is False
    assert results == [
        {
            "profile": "default",
            "status": "FAIL",
            "reason": "policy-bearing or byte-exact protected data changed",
            "raw_changed": False,
        }
    ]


def test_compare_passes_when_only_volatile_raw_digest_changes():
    passed, results = MODULE.compare(
        evidence({"default": fingerprint(raw="raw-before")}),
        evidence({"default": fingerprint(raw="raw-after")}),
        expected_profiles=["default"],
    )

    assert passed is True
    assert results == [
        {
            "profile": "default",
            "status": "PASS",
            "reason": "normalized policy aggregate unchanged",
            "raw_changed": True,
        }
    ]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ([], evidence({"default": fingerprint()})),
        (evidence({"default": {}}), evidence({"default": fingerprint()})),
    ],
)
def test_compare_cli_returns_controlled_fail_for_malformed_inputs(tmp_path, before, after):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    output_path = tmp_path / "comparison.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "compare",
            "--before",
            str(before_path),
            "--after",
            str(after_path),
            "--profiles",
            "default",
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "PROTECTED_PROFILE_GATE=FAIL" in result.stdout
    assert "Traceback" not in result.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["gate"] == "FAIL"
