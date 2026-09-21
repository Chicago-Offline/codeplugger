"""The site codeplug builder must fail loudly rather than publish nothing.

These tests cover the parts that do not need the source repositories checked
out. The build itself runs in CI, where the checkouts exist; its failure mode
is the point, so the assertions here are mostly about errors being raised.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILDER = REPO_ROOT / "tools" / "build_site_artifacts.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_site_artifacts", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def test_every_configured_source_is_well_formed():
    for source in build.load_sources():
        assert source["id"]
        assert source["checkout"]
        assert source["ssrf_roots"], f"{source['id']}: needs at least one SSRF root"
        assert source["profiles"], f"{source['id']}: lists no profiles"
        for rel in source["profiles"]:
            assert not Path(rel).is_absolute(), f"{rel} must be repo-relative"


def test_configured_profiles_name_a_known_radio_directory():
    """profiles/<radio>/<name>.yml must point at a radio we actually support."""
    radios = {p.parent.name for p in REPO_ROOT.glob("radios/*/capabilities.json")}
    for source in build.load_sources():
        for rel in source["profiles"]:
            radio_id = Path(rel).parent.name
            assert radio_id in radios, f"{rel}: unknown radio {radio_id!r}"


def test_formats_come_from_the_support_table():
    rows = build.load_radio_rows()
    caps = json.loads((REPO_ROOT / "radios/baofeng_dm32/capabilities.json").read_text())
    assert build.formats_for_radio("baofeng_dm32", rows["baofeng_dm32"], caps) == [
        "qdmr-yaml"
    ]


def test_benlink_block_adds_a_plan_download():
    rows = build.load_radio_rows()
    formats = build.formats_for_radio(
        "vero_vrn76", rows["vero_vrn76"], {"benlink": {"transport": "ble"}}
    )
    assert "benlink-plan" in formats


def test_radio_with_no_buildable_format_is_an_error():
    """A P4-style radio cannot be published: its export needs a device read."""
    with pytest.raises(build.BuildError, match="p64_toml"):
        build.formats_for_radio(
            "retevis_matetalk_p4",
            {"chirp_csv": False, "qdmr_yaml": False, "p64_toml": True},
            {},
        )


def test_missing_checkout_names_the_path(tmp_path):
    with pytest.raises(build.BuildError, match="nope"):
        build.resolve_checkout(tmp_path, "nope", where="test")


def test_every_configured_radio_is_publishable():
    """Catch a profile added for a radio that has no downloadable export."""
    rows = build.load_radio_rows()
    for source in build.load_sources():
        for rel in source["profiles"]:
            radio_id = Path(rel).parent.name
            caps = json.loads(
                (REPO_ROOT / "radios" / radio_id / "capabilities.json").read_text()
            )
            assert build.formats_for_radio(radio_id, rows[radio_id], caps)
