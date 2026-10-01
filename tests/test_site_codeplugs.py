"""The site codeplug builder must fail loudly rather than publish nothing.

These tests cover the parts that do not need the source repositories checked
out. The build itself runs in CI, where the checkouts exist; its failure mode
is the point, so the assertions here are mostly about errors being raised.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_profiles_for_same_radio_get_distinct_artifact_directories(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles" / "radio_a"
    profiles.mkdir(parents=True)
    (tmp_path / "ssrf").mkdir()
    for name in ("first", "second"):
        (profiles / f"{name}.yml").touch()

    def fake_build_profile(profile_path, ssrf_roots, radio_rows, source_dir):
        profile_id = profile_path.stem
        out_dir = source_dir / "radio_a" / profile_id
        out_dir.mkdir(parents=True)
        (out_dir / "SHA256SUMS").write_text(profile_id)
        return {"profile_id": profile_id, "radio": "radio_a", "dir": f"radio_a/{profile_id}"}

    monkeypatch.setattr(build, "build_profile", fake_build_profile)
    source = {
        "id": "shared", "name": "Shared", "checkout": "profiles",
        "ssrf_roots": [{"checkout": "ssrf", "path": "."}],
        "profiles": ["radio_a/first.yml", "radio_a/second.yml"],
    }

    result = build.build_source(source, tmp_path, {}, tmp_path / "out")

    for name, entry in zip(("first", "second"), result["profiles"]):
        assert entry["dir"] == f"shared/radio_a/{name}"
        assert (tmp_path / "out" / entry["dir"] / "SHA256SUMS").read_text() == name


def test_build_profile_writes_checksums_beside_its_files(tmp_path, monkeypatch):
    from codeplugger import artifacts, profile, resolved

    codeplug = SimpleNamespace(
        radio_id="radio_a", zones=(), channels=(), to_json=lambda: "{}",
    )
    report = SimpleNamespace(non_critical=lambda: ())
    monkeypatch.setattr(
        profile, "_load_and_validate_profile",
        lambda *args, **kwargs: ({"id": "first"}, (), None, report),
    )
    monkeypatch.setattr(profile, "_load_capabilities", lambda *args: {"name": "Radio A"})
    monkeypatch.setattr(resolved, "build_codeplug", lambda *args, **kwargs: codeplug)
    monkeypatch.setattr(artifacts, "html_reference_from_resolved", lambda *args, **kwargs: "<p>first</p>")
    monkeypatch.setattr(build, "formats_for_radio", lambda *args: ["chirp-csv"])
    monkeypatch.setattr(build, "render_exports", lambda *args: {".csv": "first\n"})

    entry = build.build_profile(
        tmp_path / "first.yml", [], {"radio_a": {}}, tmp_path / "shared",
    )

    out_dir = tmp_path / "shared" / entry["dir"]
    assert (out_dir / "first.csv").read_text() == "first\n"
    assert {line.split("  ")[1] for line in (out_dir / "SHA256SUMS").read_text().splitlines()} == {
        "first.csv", "first.html", "first.resolved.json",
    }


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
