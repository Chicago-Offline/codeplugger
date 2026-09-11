"""Tests for the NeonPlug exporter (issue #4, profile 0.1: analog only).

Discovery-gate findings (see ``codeplugger.exporters.neonplug`` package
docstring for full detail and citations against the pinned NeonPlug
revision, ``infamy/NeonPlug`` @ ``8ae184770e03a93959f81c262f2ba9dcb93b0400``):

* Generation is **base-free**: NeonPlug's own ``jsonSafeToCodeplug()``
  defaults every top-level field, so a document containing only
  ``{"version", "channels", "zones"}`` imports without any base/template
  export. No base-template contract is used or needed.
* The fields NeonPlug itself requires for a safe write are exactly its own
  ``validateChannelForEncoding()`` list; every other channel field is filled
  from NeonPlug's own ``createDefaultChannel()`` ("new channel") defaults,
  ported verbatim in ``channel_defaults.py`` with attribution.
* ``test_neonplug_import_and_dm32uv_codec_roundtrip`` below actually runs a
  generated fixture through NeonPlug's real import path and real DM-32UV
  byte codec (``encodeChannel``/``parseChannel``/``encodeZone``/
  ``parseZones``) at the pinned commit, confirming RX/TX frequency, TX
  prohibition, and CTCSS/DCS tones survive unchanged. It is skipped unless
  a local NeonPlug checkout and ``node``/``npx`` are available (mirrors this
  repo's ``dmrconf`` skip-if-uninstalled convention) -- see its docstring
  for the exact reproduction steps.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from codeplugger.exporters.neonplug import (
    FORMAT_VERSION,
    neonplug_document_from_resolved,
    write_neonplug,
)
from codeplugger.exporters.neonplug.channel_defaults import CODEPLUG_JSON_FILENAME
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedTones,
    ResolvedZone,
)


def _channel(**overrides) -> ResolvedChannel:
    values = dict(
        reference="a1",
        assignment_id="a1",
        display_name="SIMPLEX",
        rx_frequency_mhz=446.0,
        tx_frequency_mhz=446.0,
        mode="FM",
        service="pmr",
        tones=ResolvedTones(),
        tx_permitted=True,
        bandwidth_khz=12.5,
        power_w=1.0,
    )
    values.update(overrides)
    return ResolvedChannel(**values)


def _codeplug(channels: tuple[ResolvedChannel, ...], *, zones=None, **extra) -> ResolvedCodeplug:
    if zones is None:
        zones = (ResolvedZone("z1", "Zone 1", tuple(ch.reference for ch in channels)),)
    return ResolvedCodeplug(
        radio_id="baofeng_dm32",
        radio_instance_id="dm32_01",
        radio_instance=None,
        channels=channels,
        zones=zones,
        **extra,
    )


def test_channel_fields_and_zone_reference_resolution() -> None:
    codeplug = _codeplug(
        (
            _channel(tones=ResolvedTones(ctcss_tx_hz=100.0, ctcss_rx_hz=100.0)),
            _channel(reference="a2", assignment_id="a2", display_name="WIDE"),
        )
    )
    document = neonplug_document_from_resolved(codeplug)

    assert document["version"] == FORMAT_VERSION
    ch1, ch2 = document["channels"]
    assert ch1["number"] == 1
    assert ch2["number"] == 2
    assert ch1["name"] == "SIMPLEX"
    assert ch1["mode"] == "Analog"
    assert ch1["rxFrequency"] == 446.0
    assert ch1["txFrequency"] == 446.0
    assert ch1["forbidTx"] is False
    assert ch1["bandwidth"] == "12.5kHz"
    assert ch1["rxCtcssDcs"] == {"type": "CTCSS", "value": 100.0}
    assert ch1["txCtcssDcs"] == {"type": "CTCSS", "value": 100.0}

    # Zone membership resolves stable channel_references to the generated
    # channel numbers, in order -- never by display name.
    assert document["zones"][0]["channels"] == [1, 2]


def test_channel_and_zone_ordering_matches_resolved_codeplug() -> None:
    channels = tuple(
        _channel(reference=f"a{i}", assignment_id=f"a{i}", display_name=f"CH{i}")
        for i in (3, 1, 2)
    )
    codeplug = _codeplug(channels)
    document = neonplug_document_from_resolved(codeplug)

    assert [c["name"] for c in document["channels"]] == ["CH3", "CH1", "CH2"]
    assert [c["number"] for c in document["channels"]] == [1, 2, 3]
    # The zone fixture references channels in resolved (not sorted) order.
    assert document["zones"][0]["channels"] == [1, 2, 3]


def test_tx_prohibited_channel_reuses_rx_frequency_and_sets_forbid_tx() -> None:
    codeplug = _codeplug((_channel(tx_frequency_mhz=None, tx_permitted=False),))
    document = neonplug_document_from_resolved(codeplug)

    channel = document["channels"][0]
    assert channel["forbidTx"] is True
    assert channel["txFrequency"] == channel["rxFrequency"] == 446.0


def test_dcs_tone_polarity_mapping() -> None:
    codeplug = _codeplug(
        (_channel(tones=ResolvedTones(dcs_tx_code="D023N", dcs_rx_code="023I")),)
    )
    document = neonplug_document_from_resolved(codeplug)

    channel = document["channels"][0]
    assert channel["txCtcssDcs"] == {"type": "DCS", "value": 23, "polarity": "N"}
    assert channel["rxCtcssDcs"] == {"type": "DCS", "value": 23, "polarity": "P"}


def test_mixed_ctcss_and_dcs_rejected() -> None:
    codeplug = _codeplug(
        (_channel(tones=ResolvedTones(ctcss_tx_hz=100.0, dcs_tx_code="D023N")),)
    )
    with pytest.raises(ValueError, match="both CTCSS and DCS"):
        neonplug_document_from_resolved(codeplug)


@pytest.mark.parametrize("mode", ["DMR", "AM", "NFM"])
def test_unsupported_mode_rejected(mode: str) -> None:
    codeplug = _codeplug((_channel(mode=mode),))
    with pytest.raises(ValueError, match="not supported"):
        neonplug_document_from_resolved(codeplug)


def test_missing_bandwidth_without_default_rejected() -> None:
    codeplug = _codeplug((_channel(bandwidth_khz=None),))
    with pytest.raises(ValueError, match="no bandwidth"):
        neonplug_document_from_resolved(codeplug)


def test_missing_bandwidth_uses_explicit_caller_default() -> None:
    codeplug = _codeplug((_channel(bandwidth_khz=None),))
    document = neonplug_document_from_resolved(codeplug, analog_bandwidth_khz=25.0)
    assert document["channels"][0]["bandwidth"] == "25kHz"


def test_unrepresentable_bandwidth_rejected() -> None:
    codeplug = _codeplug((_channel(bandwidth_khz=20.0),))
    with pytest.raises(ValueError, match="not representable"):
        neonplug_document_from_resolved(codeplug)


@pytest.mark.parametrize(
    ("power_w", "expected"),
    [(None, "High"), (0.5, "Low"), (1.0, "Low"), (2.0, "Medium"), (2.5, "Medium"), (5.0, "High")],
)
def test_power_mapping_thresholds(power_w: float | None, expected: str) -> None:
    codeplug = _codeplug((_channel(power_w=power_w),))
    document = neonplug_document_from_resolved(codeplug)
    assert document["channels"][0]["power"] == expected


def test_zone_reference_to_missing_channel_raises_actionable_error() -> None:
    codeplug = _codeplug(
        (_channel(),),
        zones=(ResolvedZone("z1", "Zone 1", ("a1", "does-not-exist")),),
    )
    with pytest.raises(ValueError, match="does-not-exist"):
        neonplug_document_from_resolved(codeplug)


def test_zone_duplicate_reference_raises() -> None:
    codeplug = _codeplug(
        (_channel(),),
        zones=(ResolvedZone("z1", "Zone 1", ("a1", "a1")),),
    )
    with pytest.raises(ValueError, match="more than once"):
        neonplug_document_from_resolved(codeplug)


def test_duplicate_channel_reference_raises() -> None:
    codeplug = _codeplug(
        (
            _channel(),
            _channel(display_name="OTHER"),  # same reference "a1"
        ),
        zones=(),
    )
    with pytest.raises(ValueError, match="duplicate channel reference"):
        neonplug_document_from_resolved(codeplug)


def test_channel_name_too_long_raises() -> None:
    codeplug = _codeplug((_channel(display_name="THIS NAME IS WAY TOO LONG"),))
    with pytest.raises(ValueError, match="16-char limit"):
        neonplug_document_from_resolved(codeplug)


def test_zone_name_too_long_raises() -> None:
    codeplug = _codeplug(
        (_channel(),),
        zones=(ResolvedZone("z1", "TOO LONG ZONE NAME", ("a1",)),),
    )
    with pytest.raises(ValueError, match="10-char limit"):
        neonplug_document_from_resolved(codeplug)


def test_non_ascii_name_raises() -> None:
    codeplug = _codeplug((_channel(display_name="R\u00e9p\u00e9teur"),))
    with pytest.raises(ValueError, match="non-ASCII"):
        neonplug_document_from_resolved(codeplug)


def test_default_channel_fields_cover_encoding_requirements() -> None:
    """Every field NeonPlug's own validateChannelForEncoding() requires is set."""
    codeplug = _codeplug((_channel(),))
    document = neonplug_document_from_resolved(codeplug)
    channel = document["channels"][0]

    required = {
        "number",
        "name",
        "rxFrequency",
        "txFrequency",
        "mode",
        "bandwidth",
        "power",
        "rxCtcssDcs",
        "txCtcssDcs",
        "rxSquelchMode",
        "signalingType",
        "pttIdType",
    }
    for field in required:
        assert channel.get(field) is not None, f"missing required field {field!r}"


def test_write_neonplug_produces_zip_with_codeplug_json(tmp_path: Path) -> None:
    codeplug = _codeplug((_channel(),))
    out = tmp_path / "test.neonplug"
    write_neonplug(out, codeplug)

    assert out.exists()
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist() == [CODEPLUG_JSON_FILENAME]
        document = json.loads(archive.read(CODEPLUG_JSON_FILENAME))
        assert document["version"] == FORMAT_VERSION
        assert document["channels"][0]["name"] == "SIMPLEX"


def test_write_neonplug_is_deterministic(tmp_path: Path) -> None:
    codeplug = _codeplug(
        (
            _channel(),
            _channel(reference="a2", assignment_id="a2", display_name="WIDE"),
        )
    )
    out1 = tmp_path / "one.neonplug"
    out2 = tmp_path / "two.neonplug"
    write_neonplug(out1, codeplug)
    write_neonplug(out2, codeplug)

    assert out1.read_bytes() == out2.read_bytes()


def test_empty_codeplug_writes_valid_archive(tmp_path: Path) -> None:
    codeplug = _codeplug((), zones=())
    out = tmp_path / "empty.neonplug"
    write_neonplug(out, codeplug)

    with zipfile.ZipFile(out) as archive:
        document = json.loads(archive.read(CODEPLUG_JSON_FILENAME))
    assert document["channels"] == []
    assert document["zones"] == []


NEONPLUG_REPO_PATH = os.environ.get("NEONPLUG_REPO_PATH")
_VERIFY_FIXTURE_TS = Path(__file__).parent / "neonplug_verification" / "codeplugger_fixture.test.ts"


def _neonplug_checkout_ready() -> bool:
    if not NEONPLUG_REPO_PATH:
        return False
    repo = Path(NEONPLUG_REPO_PATH)
    return (repo / "package.json").exists() and (repo / "node_modules").exists()


@pytest.mark.skipif(
    shutil.which("npx") is None or not _neonplug_checkout_ready(),
    reason=(
        "requires `npx` plus a local NeonPlug checkout with `npm install` run, "
        "pointed to by NEONPLUG_REPO_PATH: "
        "git clone https://github.com/infamy/NeonPlug.git && "
        "cd NeonPlug && git checkout 8ae184770e03a93959f81c262f2ba9dcb93b0400 && "
        "npm install"
    ),
)
def test_neonplug_import_and_dm32uv_codec_roundtrip(tmp_path: Path) -> None:
    """Round-trip a generated fixture through NeonPlug's real import + DM-32UV codec.

    Copies ``neonplug_verification/codeplugger_fixture.test.ts`` into the
    checked-out NeonPlug repo's ``tests/unit/`` and runs it with that repo's
    own ``vitest``, so the assertions execute NeonPlug's real
    ``importCodeplug``, ``validateChannelForEncoding``, and DM-32UV
    ``encodeChannel``/``parseChannel``/``encodeZone``/``parseZones``
    functions at the pinned commit -- not a reimplementation.
    """
    channels = (
        _channel(
            rx_frequency_mhz=146.520,
            tx_frequency_mhz=146.520,
            tones=ResolvedTones(ctcss_tx_hz=100.0, ctcss_rx_hz=100.0),
            power_w=5.0,
        ),
        _channel(
            reference="a2",
            assignment_id="a2",
            display_name="REPEATER",
            rx_frequency_mhz=146.940,
            tx_frequency_mhz=146.340,
            tones=ResolvedTones(dcs_tx_code="D023N", dcs_rx_code="023I"),
            bandwidth_khz=25.0,
            power_w=5.0,
        ),
        _channel(
            reference="a3",
            assignment_id="a3",
            display_name="RXONLY",
            rx_frequency_mhz=162.550,
            tx_frequency_mhz=None,
            tx_permitted=False,
            power_w=1.0,
        ),
    )
    codeplug = _codeplug(channels)
    fixture_path = tmp_path / "fixture.neonplug"
    write_neonplug(fixture_path, codeplug)

    repo = Path(NEONPLUG_REPO_PATH)  # type: ignore[arg-type]
    dest = repo / "tests" / "unit" / "codeplugger_fixture.test.ts"
    original = dest.read_text() if dest.exists() else None
    dest.write_text(_VERIFY_FIXTURE_TS.read_text())
    try:
        result = subprocess.run(
            ["npx", "vitest", "run", "tests/unit/codeplugger_fixture.test.ts"],
            cwd=repo,
            env={**os.environ, "CODEPLUGGER_FIXTURE": str(fixture_path)},
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        if original is None:
            dest.unlink(missing_ok=True)
        else:
            dest.write_text(original)

    assert result.returncode == 0, result.stdout + result.stderr
