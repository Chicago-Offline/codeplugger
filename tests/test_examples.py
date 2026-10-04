"""Every shipped example must validate AND export, not merely load.

Examples are the first thing a new user runs, and a validate-only check has
already let unexportable profiles through once (the qdmr exporter raises on
conditions the validator only warns about). Each example is therefore pushed
through the same exporters the site builder uses for that radio, and the
fleet dry run must mark every example instance ready.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from codeplugger.fleet import plan_registry
from codeplugger.profile import (
    _load_and_validate_profile,
    _load_capabilities,
    installed_ssrf_root,
)
from codeplugger.resolved import build_codeplug

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
PROFILES = sorted((EXAMPLES / "profiles").rglob("*.yml"))
REGISTRY = EXAMPLES / "instances.yml"
RADIOS = REPO_ROOT / "radios"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_site_artifacts", REPO_ROOT / "tools" / "build_site_artifacts.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def test_examples_exist() -> None:
    assert PROFILES, "examples/profiles has no profiles"
    assert REGISTRY.is_file()


@pytest.mark.parametrize("profile_path", PROFILES, ids=lambda p: p.parent.name)
def test_example_validates_and_exports(profile_path: Path) -> None:
    profile, documents, instance_metadata, report = _load_and_validate_profile(
        profile_path,
        [installed_ssrf_root()],
        radio_root=RADIOS,
        instance_registry_path=REGISTRY,
    )
    assert profile_path.parent.name == profile["radio"], (
        "example directory must be named after its radio"
    )
    codeplug = build_codeplug(
        profile, documents, instance_metadata, radio_root=RADIOS, report=report
    )
    assert codeplug.channels

    capabilities = _load_capabilities(codeplug.radio_id, RADIOS)
    row = build.load_radio_rows()[codeplug.radio_id]
    formats = build.formats_for_radio(codeplug.radio_id, row, capabilities)
    exports = build.render_exports(codeplug, formats, capabilities)
    assert exports
    for extension, body in exports.items():
        assert body.strip(), f"{profile_path.name}: empty {extension} export"


def test_example_fleet_is_ready() -> None:
    plan = plan_registry(
        REGISTRY,
        EXAMPLES / "profiles",
        [installed_ssrf_root()],
        radio_root=RADIOS,
    )
    errors = {radio.id: radio.error for radio in plan.radios if radio.status != "ready"}
    assert not errors, errors
    assert len(plan.radios) == len(PROFILES), (
        "every example profile needs a registry instance and vice versa"
    )
