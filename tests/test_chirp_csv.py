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


def test_chirp_csv_sets_tx_off_for_receive_only_channels() -> None:
    channels = [
        ResolvedChannel(
            reference="wx1",
            assignment_id="wx1",
            display_name="WX1",
            rx_frequency_mhz=162.550,
            tx_frequency_mhz=None,
            mode="FM",
            service="weather",
            tones=ResolvedTones(),
            tx_permitted=False,
            notes=None,
        )
    ]
    text = chirp_csv_from_resolved(_codeplug(channels))
    row = list(csv.DictReader(io.StringIO(text)))[0]

    assert row["Duplex"] == "off"
    assert row["Offset"] == "0.000000"


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
