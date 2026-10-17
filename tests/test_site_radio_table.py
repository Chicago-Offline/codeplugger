"""The site radio table has to stay in sync with radios/*/capabilities.json.

These tests are the guard rail that was missing when vero_vrn76 was merged and
the site silently kept showing 14 radios.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "tools" / "gen_radio_table.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_radio_table", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def test_committed_index_html_is_generated():
    """site/index.html matches what the generator would write."""
    current = gen.INDEX_HTML.read_text()
    assert current == gen.render_index(current), (
        "site/index.html is stale -- run: python tools/gen_radio_table.py"
    )


@pytest.mark.parametrize("lang", sorted(gen.PAGES))
def test_every_language_page_is_generated(lang):
    """Each translated landing page carries a current table in its language."""
    path = gen.PAGES[lang]
    current = path.read_text()
    assert current == gen.render_index(current, lang=lang), (
        f"{path.relative_to(REPO_ROOT)} is stale -- run: python tools/gen_radio_table.py"
    )
    assert f'<html lang="{lang}">' in current
    assert gen.STRINGS[lang]["hw"] in current


def test_spanish_table_has_no_english_generator_strings():
    tbody = gen.build_tbody(lang="es")
    assert "Not supported" not in tbody
    assert "Verified on a physical radio" not in tbody
    assert gen.STRINGS["es"]["not_supported"] in tbody


def test_every_radio_has_a_site_row():
    caps = gen.load_capabilities()
    listed = {row["id"] for row in gen.load_rows()}
    assert set(caps) == listed


def test_row_count_matches_radio_count():
    caps = gen.load_capabilities()
    tbody = gen.build_tbody()
    assert tbody.count("<tr>") == len(caps)


def test_missing_radio_row_is_an_error(monkeypatch):
    """Adding a radio without a site row fails generation."""
    caps = gen.load_capabilities()
    caps["acme_xyz1"] = {"id": "acme_xyz1", "name": "Acme XYZ1", "modes": ["FM"]}
    monkeypatch.setattr(gen, "load_capabilities", lambda: caps)

    with pytest.raises(gen.GenError, match="acme_xyz1"):
        gen.build_tbody()


def test_orphaned_site_row_is_an_error(monkeypatch):
    rows = gen.load_rows()
    rows.append({"id": "ghost_radio", "vendor": "gone"})
    monkeypatch.setattr(gen, "load_rows", lambda: rows)

    with pytest.raises(gen.GenError, match="ghost_radio"):
        gen.build_tbody()


def test_backend_disagreement_is_an_error():
    """site/radios.json cannot claim a backend capabilities.json contradicts."""
    cap = {"id": "test", "name": "Test", "modes": ["FM"], "chirp": {}}
    with pytest.raises(gen.GenError, match="benlink"):
        gen.derive_backend(cap, "benlink")


def test_backend_without_capabilities_block_is_an_error():
    cap = {"id": "test", "name": "Test", "modes": ["FM"]}
    with pytest.raises(gen.GenError, match="no matching block"):
        gen.derive_backend(cap, "chirp-writer")


AIRBAND = {"name": "Airband", "min_mhz": 108.0, "max_mhz": 136.0, "rx_only": True}


def test_modes_render_in_a_fixed_order():
    cap = {"id": "test", "name": "Test", "modes": ["AM", "FM", "DMR"], "bands": [AIRBAND]}
    assert gen.derive_modes(cap) == "DMR · FM · AM RX"


def test_am_without_rx_only_band_is_an_error():
    cap = {
        "id": "test",
        "name": "Test",
        "modes": ["FM", "AM"],
        "bands": [{"name": "VHF", "min_mhz": 136.0, "max_mhz": 174.0}],
    }
    with pytest.raises(gen.GenError, match="rx_only"):
        gen.derive_modes(cap)


def test_unknown_mode_is_an_error():
    cap = {"id": "test", "name": "Test", "modes": ["FM", "P25"]}
    with pytest.raises(gen.GenError, match="P25"):
        gen.derive_modes(cap)


def test_uv5r_mini_shows_am_rx():
    """Regression: capabilities declared FM only until 2026-09-16, which would
    have silently dropped this radio's airband RX from the table."""
    caps = gen.load_capabilities()
    assert caps["baofeng_uv5r_mini"]["modes"] == ["FM", "AM"]
    assert 'class="modes">FM · AM RX' in gen.build_tbody()


def test_modes_is_not_duplicated_in_site_data():
    """Modes is derived; restating it in site/radios.json would let it drift."""
    for row in gen.load_rows():
        assert "modes" not in row, f"{row['id']}: drop 'modes', it is derived"


def test_site_rows_use_known_columns():
    data = json.loads(gen.RADIOS_JSON.read_text())
    allowed = {"id", "vendor", "headless", *gen.ARTIFACT_COLUMNS}
    for row in data["rows"]:
        extra = set(row) - allowed
        assert not extra, f"{row.get('id')}: unknown key(s) {sorted(extra)}"


def _manifest(*entries: dict) -> dict:
    return {"sources": [{"id": "s", "profiles": list(entries)}]}


def test_codeplug_links_only_appear_for_radios_that_have_one():
    tbody = gen.build_tbody(
        {"baofeng_bf888s": [{"profile_id": "example_bf888s_gmrs", "radio": "baofeng_bf888s"}]}
    )

    assert 'href="codeplugs/#example_bf888s_gmrs">Codeplug</a>' in tbody
    assert tbody.count('class="codeplug"') == 1


def test_several_codeplugs_for_one_radio_are_counted():
    tbody = gen.build_tbody(
        {
            "baofeng_dm32": [
                {"profile_id": "first", "radio": "baofeng_dm32"},
                {"profile_id": "second", "radio": "baofeng_dm32"},
            ]
        }
    )

    assert 'href="codeplugs/#first">2 codeplugs</a>' in tbody


def test_table_without_a_manifest_has_no_links():
    """The committed table must stay link-free; links are a deploy-time thing."""
    assert "codeplug" not in gen.build_tbody()


def test_manifest_naming_an_unknown_radio_is_an_error():
    with pytest.raises(gen.GenError, match="acme_xyz1"):
        gen.build_tbody({"acme_xyz1": [{"profile_id": "p", "radio": "acme_xyz1"}]})


def test_load_codeplugs_indexes_by_radio(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps(
            _manifest(
                {"profile_id": "a", "radio": "radio_one"},
                {"profile_id": "b", "radio": "radio_two"},
            )
        )
    )

    assert gen.load_codeplugs(path) == {
        "radio_one": [{"profile_id": "a", "radio": "radio_one"}],
        "radio_two": [{"profile_id": "b", "radio": "radio_two"}],
    }


def test_empty_manifest_is_an_error(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"sources": []}))

    with pytest.raises(gen.GenError, match="no codeplugs"):
        gen.load_codeplugs(path)


def test_check_refuses_a_manifest(capsys):
    """--check compares against the committed file, which never has links."""
    with pytest.raises(SystemExit):
        gen.main(["--check", "--codeplugs", "site/codeplugs/index.json"])
    assert "--check" in capsys.readouterr().err
