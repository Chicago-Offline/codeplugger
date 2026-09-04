from __future__ import annotations

import tomllib

import pytest

from codeplugger.exporters.p64_toml import p64_toml_from_resolved
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedTones,
    ResolvedZone,
)


def test_p64_toml_preserves_baseline_and_updates_profile_records() -> None:
    channel = ResolvedChannel(
        reference="asg1",
        assignment_id="asg1",
        display_name="FAMILY",
        rx_frequency_mhz=462.575,
        tx_frequency_mhz=462.575,
        mode="DMR",
        service="gmrs",
        tones=ResolvedTones(),
        tx_permitted=True,
        bandwidth_khz=12.5,
        power_w=0.5,
        color_code=1,
        timeslot=1,
    )
    codeplug = ResolvedCodeplug(
        radio_id="retevis_matetalk_p4",
        radio_instance_id="p4_02",
        radio_instance=None,
        channels=(channel,),
        zones=(ResolvedZone("family", "Family", ("asg1",)),),
    )
    baseline = """
[radio]
name = "P4"
[general]
radio_dmr_id = 682041
[[channel]]
index = 1
name = "OLD"
mode = "analog"
rx_mhz = 446.0
tx_mhz = 446.0
power = "low"
[[zone]]
index = 1
name = "Old zone"
channels = [1]
"""

    result = tomllib.loads(
        p64_toml_from_resolved(
            codeplug,
            baseline,
            fleet_instances={
                "p4_01": {"dmr_id": 682041},
                "p4_02": {"dmr_id": 682042, "dmr_contact_name": "682042"},
            },
            digital_contact_index=1,
            digital_rx_group_index=1,
        )
    )

    assert result["general"]["radio_dmr_id"] == 682042
    assert result["contact"][-1] == {
        "index": 2,
        "name": "682042",
        "dmr_id": 682042,
        "call_type": "private",
    }
    assert result["channel"][0]["name"] == "FAMILY"
    assert result["channel"][0]["mode"] == "digital"
    assert result["channel"][0]["color_code"] == 1
    assert result["channel"][0]["contact"] == 1
    assert result["channel"][0]["rx_group"] == 1
    assert result["channel"][0].get("rx_tone") is None
    assert result["zone"][0]["name"] == "Family"


def test_p64_toml_rejects_insufficient_baseline_capacity() -> None:
    channel = ResolvedChannel(
        "asg1", "asg1", "ONE", 446.0, 446.0, "FM", "pmr", ResolvedTones(), True
    )
    codeplug = ResolvedCodeplug("retevis_matetalk_p4", "p4_01", None, (channel,), ())

    with pytest.raises(ValueError, match=r"missing \[\[channel\]\]"):
        p64_toml_from_resolved(codeplug, "[radio]\nname = 'P4'\n")


def test_p64_toml_can_clear_unused_baseline_channels() -> None:
    channel = ResolvedChannel(
        "asg1", "asg1", "ONE", 446.0, 446.0, "FM", "pmr", ResolvedTones(), True
    )
    codeplug = ResolvedCodeplug("retevis_matetalk_p4", "p4_01", None, (channel,), ())
    baseline = """
[radio]
[general]
[[channel]]
index = 1
name = "OLD"
mode = "analog"
rx_mhz = 446.0
tx_mhz = 446.0
power = "low"
[[channel]]
index = 2
name = "STALE"
mode = "analog"
rx_mhz = 462.575
tx_mhz = 462.575
power = "low"
[[zone]]
index = 1
name = "Old zone"
channels = [1]
"""

    result = tomllib.loads(
        p64_toml_from_resolved(codeplug, baseline, clear_unused_channels=True)
    )

    assert result["channel"][1]["name"] == ""
    assert result["channel"][1]["rx_mhz"] == 0.0


def test_p64_toml_can_clear_unused_baseline_zones() -> None:
    channel = ResolvedChannel(
        "asg1", "asg1", "ONE", 446.0, 446.0, "FM", "pmr", ResolvedTones(), True
    )
    codeplug = ResolvedCodeplug(
        "retevis_matetalk_p4",
        "p4_01",
        None,
        (channel,),
        (ResolvedZone("one", "One", ("asg1",)),),
    )

    result = tomllib.loads(
        p64_toml_from_resolved(
            codeplug,
            """
[radio]
[general]
[[channel]]
index = 1
name = "OLD"
mode = "analog"
rx_mhz = 446.0
tx_mhz = 446.0
power = "low"
[[zone]]
index = 1
name = "One"
channels = [1]
[[zone]]
index = 2
name = "STALE"
channels = [1]
""",
            clear_unused_channels=True,
        )
    )

    assert result["zone"][1]["name"] == ""
    assert result["zone"][1]["channels"] == []


def test_p64_toml_exports_ctcss_tones() -> None:
    channel = ResolvedChannel(
        "asg1",
        "asg1",
        "ROAD",
        462.675,
        462.675,
        "FM",
        "gmrs",
        ResolvedTones(ctcss_tx_hz=141.3, ctcss_rx_hz=141.3),
        True,
    )
    codeplug = ResolvedCodeplug("retevis_matetalk_p4", "p4_02", None, (channel,), ())

    result = tomllib.loads(
        p64_toml_from_resolved(
            codeplug,
            """
[radio]
[general]
[[channel]]
index = 1
name = "OLD"
mode = "analog"
rx_mhz = 462.675
tx_mhz = 462.675
power = "low"
[[zone]]
index = 1
name = "Old zone"
channels = [1]
""",
        )
    )

    assert result["channel"][0]["rx_tone"] == "141.3"
    assert result["channel"][0]["tx_tone"] == "141.3"