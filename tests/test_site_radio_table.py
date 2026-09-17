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
    assert gen.render_modes(cap, ["AM RX", "DMR", "FM"]) == "DMR · FM · AM RX"


def test_site_table_may_not_hide_a_capabilities_mode():
    """If capabilities gains DMR, the table cannot keep saying FM only."""
    cap = {"id": "test", "name": "Test", "modes": ["FM", "DMR"]}
    with pytest.raises(gen.GenError, match="DMR"):
        gen.render_modes(cap, ["FM"])


def test_site_table_may_show_rx_only_am_capabilities_omits():
    """baofeng_uv5r_mini: modes is emission policy, AM RX is still true."""
    cap = {"id": "test", "name": "Test", "modes": ["FM"], "bands": [AIRBAND]}
    assert gen.render_modes(cap, ["FM", "AM RX"]) == "FM · AM RX"


def test_am_without_rx_only_band_is_an_error():
    cap = {
        "id": "test",
        "name": "Test",
        "modes": ["FM"],
        "bands": [{"name": "VHF", "min_mhz": 136.0, "max_mhz": 174.0}],
    }
    with pytest.raises(gen.GenError, match="rx_only"):
        gen.render_modes(cap, ["FM", "AM RX"])


def test_unknown_mode_token_is_an_error():
    cap = {"id": "test", "name": "Test", "modes": ["FM"]}
    with pytest.raises(gen.GenError, match="P25"):
        gen.render_modes(cap, ["FM", "P25"])


def test_uv5r_mini_keeps_its_am_rx_cell():
    """Regression: deriving modes from capabilities would drop this."""
    assert 'class="modes">FM · AM RX' in gen.build_tbody()


def test_site_rows_use_known_columns():
    data = json.loads(gen.RADIOS_JSON.read_text())
    allowed = {"id", "vendor", "modes", "headless", *gen.ARTIFACT_COLUMNS}
    for row in data["rows"]:
        extra = set(row) - allowed
        assert not extra, f"{row.get('id')}: unknown key(s) {sorted(extra)}"
