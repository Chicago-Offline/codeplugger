from __future__ import annotations

import csv
import io

import pytest

from codeplugger.exporters.chirp_csv import chirp_csv_from_resolved
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedTones,
    ResolvedZone,
)


def _codeplug(channels: list[ResolvedChannel]) -> ResolvedCodeplug:
    refs = tuple(channel.reference for channel in channels)
    return ResolvedCodeplug(
        radio_id="baofeng_uv5r_mini",
        radio_instance_id="5rm_01",
        radio_instance=None,
        channels=tuple(channels),
        zones=(ResolvedZone(id="z1", name="Reference", channel_references=refs),),
    )


def test_chirp_csv_preserves_channel_order_and_fields() -> None:
    channels = [
        ResolvedChannel(
            reference="asg_1",
            assignment_id="asg_1",
            display_name="SIMPLEX",
            rx_frequency_mhz=146.520,
            tx_frequency_mhz=146.520,
            mode="FM",
            service="amateur",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes="local simplex",
        ),
        ResolvedChannel(
            reference="asg_2",
            assignment_id="asg_2",
            display_name="RPT-",
            rx_frequency_mhz=147.390,
            tx_frequency_mhz=146.790,
            mode="FM",
            service="amateur",
            tones=ResolvedTones(ctcss_tx_hz=100.0),
            tx_permitted=True,
            notes=None,
        ),
    ]
    text = chirp_csv_from_resolved(_codeplug(channels))
    rows = list(csv.DictReader(io.StringIO(text)))

    assert [row["Location"] for row in rows] == ["1", "2"]
    assert [row["Name"] for row in rows] == ["SIMPLEX", "RPT-"]
    assert rows[0]["Frequency"] == "146.520000"
    assert rows[0]["Duplex"] == ""
    assert rows[0]["Offset"] == "0.000000"
    assert rows[0]["Comment"] == "local simplex"
    assert rows[1]["Duplex"] == "-"
    assert rows[1]["Offset"] == "0.600000"
    assert rows[1]["Tone"] == "Tone"
    assert rows[1]["rToneFreq"] == "100.0"


def _receive_only_channel() -> ResolvedChannel:
    return ResolvedChannel(
        reference="wx1",
        assignment_id="wx1",
        display_name="WX1",
        rx_frequency_mhz=162.550,
        tx_frequency_mhz=None,
        mode="FM",
        service="weather",
        tones=ResolvedTones(),
        tx_permitted=False,
        notes="receive only",
    )


def test_chirp_csv_emits_receive_only_channels_as_simplex() -> None:
    """Receive-only channels export as plain simplex, not ``Duplex=off``.

    ``off`` is rejected by CHIRP's CSV parser, and our reference codeplugs
    store receive-only blocks as ``tx == rx`` anyway.
    """

    text = chirp_csv_from_resolved(_codeplug([_receive_only_channel()]))
    row = list(csv.DictReader(io.StringIO(text)))[0]

    assert row["Duplex"] == ""
    assert row["Offset"] == "0.000000"
    assert row["Frequency"] == "162.550000"
    # The receive-only intent has to survive somewhere the parser keeps.
    assert row["Name"] == "WX1"


def test_chirp_csv_never_emits_duplex_values_chirp_cannot_parse() -> None:
    """Guard the parser contract: Duplex is always ``+``, ``-`` or empty.

    Mirrors the accepted set in ``chirp_common.really_from_csv``.
    """

    channels = [
        _receive_only_channel(),
        # A >30 MHz spread previously became "split", which CHIRP rejects.
        ResolvedChannel(
            reference="wide",
            assignment_id="wide",
            display_name="WIDE SPLIT",
            rx_frequency_mhz=145.000,
            tx_frequency_mhz=440.000,
            mode="FM",
            service="amateur",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
        ),
        ResolvedChannel(
            reference="rpt",
            assignment_id="rpt",
            display_name="RPT+",
            rx_frequency_mhz=462.550,
            tx_frequency_mhz=467.550,
            mode="FM",
            service="gmrs",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
        ),
    ]

    rows = list(csv.DictReader(io.StringIO(chirp_csv_from_resolved(_codeplug(channels)))))

    assert [row["Duplex"] for row in rows] == ["", "+", "+"]
    for row in rows:
        assert row["Duplex"] in {"+", "-", ""}


def test_chirp_csv_wide_split_offset_is_the_true_difference() -> None:
    """A wide split is emitted as a real offset, so TX is not silently lost."""

    channels = [
        ResolvedChannel(
            reference="wide",
            assignment_id="wide",
            display_name="WIDE SPLIT",
            rx_frequency_mhz=145.000,
            tx_frequency_mhz=440.000,
            mode="FM",
            service="amateur",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
        )
    ]
    row = list(csv.DictReader(io.StringIO(chirp_csv_from_resolved(_codeplug(channels)))))[0]

    assert row["Duplex"] == "+"
    assert row["Offset"] == "295.000000"


def test_chirp_csv_mode_follows_bandwidth() -> None:
    """12.5 kHz channels must import as NFM, not wide FM."""

    channels = [
        ResolvedChannel(
            reference="narrow",
            assignment_id="narrow",
            display_name="MURS",
            rx_frequency_mhz=154.600,
            tx_frequency_mhz=154.600,
            mode="FM",
            service="murs",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
            bandwidth_khz=12.5,
        ),
        ResolvedChannel(
            reference="wide",
            assignment_id="wide",
            display_name="GMRS",
            rx_frequency_mhz=462.550,
            tx_frequency_mhz=462.550,
            mode="FM",
            service="gmrs",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
            bandwidth_khz=25.0,
        ),
        ResolvedChannel(
            reference="unknown",
            assignment_id="unknown",
            display_name="UNKNOWN BW",
            rx_frequency_mhz=146.520,
            tx_frequency_mhz=146.520,
            mode="FM",
            service="amateur",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
        ),
    ]

    rows = list(csv.DictReader(io.StringIO(chirp_csv_from_resolved(_codeplug(channels)))))

    assert [row["Mode"] for row in rows] == ["NFM", "FM", "FM"]


def test_chirp_csv_omits_power_column() -> None:
    """``chirp_common.parse_power`` takes watts only and fails the whole row
    on ``""`` or a level name, so the column is left out entirely."""

    text = chirp_csv_from_resolved(_codeplug([_receive_only_channel()]))

    assert "Power" not in text.splitlines()[0].split(",")


def test_chirp_csv_rejects_non_fm_modes() -> None:
    channels = [
        ResolvedChannel(
            reference="dmr_1",
            assignment_id="dmr_1",
            display_name="DMR TG",
            rx_frequency_mhz=443.100,
            tx_frequency_mhz=448.100,
            mode="DMR",
            service="dmr",
            tones=ResolvedTones(),
            tx_permitted=True,
            notes=None,
        )
    ]

    with pytest.raises(ValueError, match="unsupported"):
        chirp_csv_from_resolved(_codeplug(channels))


def test_chirp_csv_zero_pads_dtcs_codes() -> None:
    """DTCS codes are three-digit, matching CHIRP's own "%03i" output.

    Code 23 must serialize as "023"; bare "23" is inconsistent with every
    reference export even though CHIRP's parser would accept it.
    """

    channels = [
        ResolvedChannel(
            reference="dcs",
            assignment_id="dcs",
            display_name="FAM ALL",
            rx_frequency_mhz=462.575,
            tx_frequency_mhz=462.575,
            mode="FM",
            service="gmrs",
            tones=ResolvedTones(dcs_tx_code=23, dcs_rx_code=23),
            tx_permitted=True,
            notes="All",
        )
    ]
    row = list(csv.DictReader(io.StringIO(chirp_csv_from_resolved(_codeplug(channels)))))[0]

    assert row["Tone"] == "DTCS"
    assert row["DtcsCode"] == "023"
    assert row["RxDtcsCode"] == "023"
