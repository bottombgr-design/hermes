"""Regression tests for packaging metadata in pyproject.toml."""

from pathlib import Path
import tomllib

from packaging.markers import Marker, default_environment


def _load_pyproject():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        return tomllib.load(handle)


def _load_optional_dependencies():
    return _load_pyproject()["project"]["optional-dependencies"]


def test_pillow_source_is_explicit_and_scoped_to_available_arm32_wheels():
    """Keep piwheels limited to Pillow where it publishes compatible wheels."""
    data = _load_pyproject()
    uv_config = data["tool"]["uv"]
    pillow_sources = uv_config["sources"]["pillow"]
    piwheels = next(
        index for index in uv_config["index"] if index["name"] == "piwheels"
    )

    assert piwheels["url"].rstrip("/") == "https://www.piwheels.org/simple"
    assert piwheels["explicit"] is True
    piwheels_source_keys = {
        name
        for name, sources in uv_config["sources"].items()
        for source in (sources if isinstance(sources, list) else [sources])
        if source.get("index") == "piwheels"
    }
    assert piwheels_source_keys == {"pillow"}
    assert len(pillow_sources) == 1
    assert pillow_sources[0]["index"] == "piwheels"

    marker = Marker(pillow_sources[0]["marker"])
    environment = default_environment()
    supported_machines = {"armv6l", "armv7l"}
    supported_pythons = {"3.11", "3.13"}
    for machine in ("armv6l", "armv7l", "aarch64", "arm64", "x86_64"):
        for python_version in ("3.11", "3.12", "3.13"):
            environment["platform_machine"] = machine
            environment["python_version"] = python_version
            environment["python_full_version"] = f"{python_version}.0"
            expected = machine in supported_machines and python_version in supported_pythons
            assert marker.evaluate(environment) is expected


def test_arm32_pillow_wheels_are_hash_locked():
    """The trusted ARM32 artifacts must stay inside uv's SHA256 lock chain."""
    lock_path = Path(__file__).resolve().parents[1] / "uv.lock"
    with lock_path.open("rb") as handle:
        packages = tomllib.load(handle)["package"]

    pillow = next(
        package
        for package in packages
        if package["name"] == "pillow"
        and package["source"].get("registry", "").rstrip("/")
        == "https://www.piwheels.org/simple"
    )
    wheels = pillow["wheels"]

    assert any(wheel["url"].endswith("linux_armv6l.whl") for wheel in wheels)
    assert any(wheel["url"].endswith("linux_armv7l.whl") for wheel in wheels)
    assert all(wheel["hash"].startswith("sha256:") for wheel in wheels)


def test_locked_pillow_edges_preserve_expected_source_routing():
    """Hermes must route Pillow to the intended source for each platform."""
    lock_path = Path(__file__).resolve().parents[1] / "uv.lock"
    with lock_path.open("rb") as handle:
        packages = tomllib.load(handle)["package"]

    assert any(
        package["name"] == "pillow"
        and package["source"].get("registry", "").rstrip("/")
        == "https://pypi.org/simple"
        for package in packages
    ), "PyPI Pillow entry must remain for non-ARM32 platforms"
    hermes = next(package for package in packages if package["name"] == "hermes-agent")
    pillow_edges = [
        dependency
        for dependency in hermes["dependencies"]
        if dependency["name"] == "pillow"
    ]
    edges_by_registry = {
        edge["source"].get("registry", "").rstrip("/"): edge
        for edge in pillow_edges
    }
    assert set(edges_by_registry) == {
        "https://pypi.org/simple",
        "https://www.piwheels.org/simple",
    }

    pypi_marker = Marker(edges_by_registry["https://pypi.org/simple"]["marker"])
    piwheels_marker = Marker(
        edges_by_registry["https://www.piwheels.org/simple"]["marker"]
    )
    environment = default_environment()

    for machine in ("armv6l", "armv7l", "aarch64", "arm64", "x86_64"):
        for python_version in ("3.11", "3.12", "3.13"):
            environment["platform_machine"] = machine
            environment["python_version"] = python_version
            environment["python_full_version"] = f"{python_version}.0"
            use_piwheels = (
                machine in {"armv6l", "armv7l"}
                and python_version in {"3.11", "3.13"}
            )
            assert pypi_marker.evaluate(environment) is not use_piwheels
            assert piwheels_marker.evaluate(environment) is use_piwheels


def test_matrix_extra_not_in_all():
    """The [matrix] extra pulls `mautrix[encryption]` -> `python-olm`,
    which has Linux-only wheels and no native build path on Windows or
    modern macOS (archived libolm, C++ errors with Clang 21+).

    With matrix in [all], `uv sync --locked` on Windows tried to build
    python-olm from sdist and failed on `make`. As of 2026-05-12 the
    [matrix] extra is excluded from [all] entirely and routed through
    `tools/lazy_deps.py` (LAZY_DEPS["platform.matrix"]) — installs at
    first use, where the user is expected to have a toolchain.
    """
    optional_dependencies = _load_optional_dependencies()

    assert "matrix" in optional_dependencies, "[matrix] extra must still exist for `uv sync --extra matrix`"
    # Must NOT appear in [all] in any form — neither unconditional nor
    # platform-gated. Lazy-install handles it.
    matrix_in_all = [
        dep for dep in optional_dependencies["all"]
        if "matrix" in dep
    ]
    assert not matrix_in_all, (
        "matrix must not appear in [all] — it's lazy-installed via "
        "tools/lazy_deps.py LAZY_DEPS['platform.matrix']. Found: "
        f"{matrix_in_all}"
    )


def test_lazy_installable_extras_excluded_from_all():
    """Policy (2026-05-12): every extra that has a `LAZY_DEPS` entry
    in `tools/lazy_deps.py` must be excluded from [all].

    The lazy-install system exists so one quarantined PyPI release
    (e.g. mistralai 2.4.6) can't break every fresh install. Putting a
    backend in BOTH [all] and LAZY_DEPS defeats that — fresh installs
    eager-install it and inherit whatever's broken upstream.

    If you're tempted to add an opt-in backend to [all] for "convenience,"
    add it to `LAZY_DEPS` instead so it installs at first use.
    """
    optional_dependencies = _load_optional_dependencies()

    # Hard-coded mirror of the extras that are in LAZY_DEPS as of
    # 2026-05-12. This list intentionally duplicates rather than
    # imports tools/lazy_deps.py so the test stays a contract — if
    # someone adds a new lazy-install backend, they have to update
    # this list AND verify [all] doesn't contain it.
    lazy_covered_extras = {
        "anthropic", "bedrock",
        "exa", "firecrawl", "parallel-web",
        "fal",
        "edge-tts", "tts-premium",
        "voice",  # faster-whisper / sounddevice / numpy
        "modal", "daytona",
        "messaging", "slack", "matrix", "dingtalk", "feishu",
        "honcho", "hindsight",
        "supermemory", "mem0",
        "mistral",  # mistralai — Voxtral STT/TTS, lazy-installed (stt.mistral / tts.mistral)
    }
    all_extra_specs = optional_dependencies["all"]
    for extra in lazy_covered_extras:
        offending = [
            spec for spec in all_extra_specs
            if f"hermes-agent[{extra}]" in spec
        ]
        assert not offending, (
            f"[{extra}] is in [all] but also in LAZY_DEPS. "
            f"Remove it from [all] in pyproject.toml — it lazy-installs "
            f"at first use. Found in [all]: {offending}"
        )


def _exact_pins(specs):
    pins = {}
    for spec in specs:
        requirement = spec.split(";", 1)[0].strip()
        if "==" not in requirement:
            continue
        package, version = requirement.split("==", 1)
        package = package.split("[", 1)[0].lower().replace("_", "-")
        pins[package] = version
    return pins


def test_pyproject_aiohttp_pins_match_lazy_slack_pin():
    """Avoid update/lazy-install churn from conflicting aiohttp pins.

    pyproject extras (messaging/slack/homeassistant/sms) exact-pin aiohttp.
    The Slack lazy-install deps (LAZY_DEPS['platform.slack']) also pin it.
    If the two drift, `hermes update` resolves the pyproject pin and
    downgrades aiohttp, reopening the CVEs the lazy pin fixed (#31817) —
    only for Slack's lazy refresh to upgrade it again on next use.
    """
    from tools.lazy_deps import LAZY_DEPS

    optional_dependencies = _load_optional_dependencies()
    lazy_aiohttp = _exact_pins(LAZY_DEPS["platform.slack"])["aiohttp"]

    pyproject_aiohttp_pins = {
        extra: pins["aiohttp"]
        for extra, specs in optional_dependencies.items()
        if "aiohttp" in (pins := _exact_pins(specs))
    }

    assert pyproject_aiohttp_pins, "expected at least one pyproject extra to pin aiohttp"
    mismatches = {
        extra: pin
        for extra, pin in pyproject_aiohttp_pins.items()
        if pin != lazy_aiohttp
    }
    assert not mismatches, (
        "pyproject.toml aiohttp pins must match "
        "LAZY_DEPS['platform.slack'] to avoid hermes update downgrading "
        "aiohttp before Slack's lazy refresh upgrades it again. "
        f"lazy aiohttp=={lazy_aiohttp}; mismatched extras: {mismatches}"
    )


def test_pyproject_pins_match_lazy_deps_pins():
    """Generalize #31817 to the whole pin surface, not just aiohttp.

    Any package that is exact-pinned in BOTH a pyproject extra and a
    `tools/lazy_deps.py` LAZY_DEPS entry must use the SAME version in both
    places. When they drift, `hermes update` resolves the pyproject extra
    pin and downgrades the package to the older version, reopening whatever
    the lazy pin fixed (the aiohttp #31817 case, and the anthropic
    CVE-2026-34450/34452 case found alongside it) — only for the lazy
    refresh to re-upgrade it on next feature use. The lazy pin is the
    security-current source of truth; extras must track it.
    """
    from tools.lazy_deps import LAZY_DEPS

    optional_dependencies = _load_optional_dependencies()

    # package -> version, as pinned across all pyproject extras. If an
    # extra pins a package at a different version than another extra, that
    # is itself a bug (caught below); here we just collect the set.
    pyproject_pins: dict[str, set[str]] = {}
    for specs in optional_dependencies.values():
        for package, version in _exact_pins(specs).items():
            pyproject_pins.setdefault(package, set()).add(version)

    # package -> version, as pinned across all LAZY_DEPS entries.
    lazy_pins: dict[str, set[str]] = {}
    for specs in LAZY_DEPS.values():
        if isinstance(specs, str):
            specs = (specs,)
        for package, version in _exact_pins(specs).items():
            lazy_pins.setdefault(package, set()).add(version)

    shared = sorted(set(pyproject_pins) & set(lazy_pins))
    assert shared, "expected at least one package pinned in both pyproject and LAZY_DEPS"

    drift = {
        package: {
            "pyproject": sorted(pyproject_pins[package]),
            "lazy_deps": sorted(lazy_pins[package]),
        }
        for package in shared
        if pyproject_pins[package] != lazy_pins[package]
    }
    assert not drift, (
        "pyproject extras pins must match tools/lazy_deps.py LAZY_DEPS pins "
        "for every shared package — otherwise `hermes update` downgrades the "
        "package below the security-current lazy pin (see #31817). Drift: "
        f"{drift}"
    )


def test_dev_extra_excluded_from_all():
    """End-user installs should not pull test/lint/debug tooling."""
    optional_dependencies = _load_optional_dependencies()

    assert "dev" in optional_dependencies
    assert not any(
        spec == "hermes-agent[dev]"
        for spec in optional_dependencies["all"]
    )


def test_messaging_extra_includes_qrcode_for_weixin_setup():
    optional_dependencies = _load_optional_dependencies()

    messaging_extra = optional_dependencies["messaging"]
    assert any(dep.startswith("qrcode") for dep in messaging_extra)


def test_dingtalk_extra_includes_qrcode_for_qr_auth():
    """DingTalk's QR-code device-flow auth (hermes_cli/dingtalk_auth.py)
    needs the qrcode package."""
    optional_dependencies = _load_optional_dependencies()

    dingtalk_extra = optional_dependencies["dingtalk"]
    assert any(dep.startswith("qrcode") for dep in dingtalk_extra)


def test_feishu_extra_includes_qrcode_for_qr_login():
    """Feishu's QR login flow (gateway/platforms/feishu.py) needs the
    qrcode package."""
    optional_dependencies = _load_optional_dependencies()

    feishu_extra = optional_dependencies["feishu"]
    assert any(dep.startswith("qrcode") for dep in feishu_extra)


def test_nemo_relay_extra_uses_supported_official_distribution_range():
    optional_dependencies = _load_optional_dependencies()

    assert optional_dependencies["nemo-relay"] == ["nemo-relay>=0.5,<1.0"]
    assert not any(
        spec == "hermes-agent[nemo-relay]"
        for spec in optional_dependencies["all"]
    )
