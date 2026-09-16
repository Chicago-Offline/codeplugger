from __future__ import annotations

import json

import pytest

from codeplugger.exporters.benlink_plan import (
    benlink_plan_from_resolved,
    benlink_plan_json_from_resolved,
)
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedTones,
    ResolvedZone,
)

CAPABILITIES = {
    "id": "vero_vrn76",
    "name": "Vero VR-N76",
    "limits": {
        "max_channels": 192,
        "max_zones": 6,
        "max_channels_per_zone": 32,
        "max_channel_name_chars": 10,
    },
    "benlink": {"vendor_id": 1, "product_id": 259},
}


def _channel(reference: str, name: str, **kwargs) -> ResolvedChannel:
    defaults = dict(
        assignment_id=reference,
        display_name=name,
        rx_frequency_mhz=146.520,
        tx_frequency_mhz=146.520,
        mode="FM",
        service="amateur",
        tones=ResolvedTones(),
        tx_permitted=True,
    )
    defaults.update(kwargs)
    return ResolvedChannel(reference=reference, **defaults)


def _codeplug(zones: list[tuple[str, list[ResolvedChannel]]]) -> ResolvedCodeplug:
    channels: list[ResolvedChannel] = []
    resolved_zones = []
    for index, (zone_name, zone_channels) in enumerate(zones):
        channels.extend(zone_channels)
        resolved_zones.append(
            ResolvedZone(
                id=f"z{index}",
                name=zone_name,
                channel_references=tuple(c.reference for c in zone_channels),
            )
        )
    return ResolvedCodeplug(
        radio_id="vero_vrn76",
        radio_instance_id="n76_tan",
        radio_instance=None,
        channels=tuple(channels),
        zones=tuple(resolved_zones),
    )


def test_zones_become_regions_with_one_based_slots() -> None:
    codeplug = _codeplug([
        ("Ham", [_channel("a", "2m Call"), _channel("b", "70cm", rx_frequency_mhz=446.0)]),
        ("GMRS", [_channel("c", "GMRS 18", rx_frequency_mhz=462.625)]),
    ])

    plan = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)

    assert plan["format"] == "benlink-codeplug"
    assert plan["version"] == 1
    assert [r["index"] for r in plan["regions"]] == [0, 1]
    assert [r["name"] for r in plan["regions"]] == ["Ham", "GMRS"]
    # Slots are 1-based and restart in every region, because channel IDs do.
    assert [c["slot"] for c in plan["regions"][0]["channels"]] == [1, 2]
    assert [c["slot"] for c in plan["regions"][1]["channels"]] == [1]
    assert [c["name"] for c in plan["regions"][0]["channels"]] == ["2m Call", "70cm"]


def test_radio_identity_comes_from_capabilities() -> None:
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])

    plan = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)

    assert plan["radio"]["vendor_id"] == 1
    assert plan["radio"]["product_id"] == 259
    assert plan["radio"]["region_count"] == 6
    assert plan["radio"]["channels_per_region"] == 32


def test_receive_only_channel_sets_tx_disable_and_parks_tx_on_rx() -> None:
    codeplug = _codeplug([
        ("WX", [
            _channel(
                "wx",
                "WX2",
                rx_frequency_mhz=162.400,
                tx_frequency_mhz=None,
                tx_permitted=False,
            )
        ]),
    ])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert entry["tx_disable"] is True
    assert entry["tx_mhz"] == entry["rx_mhz"] == 162.4


def test_split_repeater_pair_and_ctcss_tones() -> None:
    codeplug = _codeplug([
        ("GMRS", [
            _channel(
                "rpt",
                "RPT 18",
                rx_frequency_mhz=462.625,
                tx_frequency_mhz=467.625,
                tones=ResolvedTones(ctcss_tx_hz=141.3, ctcss_rx_hz=141.3),
            )
        ]),
    ])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert entry["rx_mhz"] == 462.625
    assert entry["tx_mhz"] == 467.625
    assert entry["tx_tone"] == {"ctcss": 141.3}
    assert entry["rx_tone"] == {"ctcss": 141.3}


def test_tone_encode_only_omits_rx_tone() -> None:
    codeplug = _codeplug([
        ("GMRS", [_channel("rpt", "RPT", tones=ResolvedTones(ctcss_tx_hz=110.9))]),
    ])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert entry["tx_tone"] == {"ctcss": 110.9}
    assert "rx_tone" not in entry


def test_dcs_tones_are_emitted_as_integers() -> None:
    codeplug = _codeplug([
        ("GMRS", [
            _channel("d", "DCS", tones=ResolvedTones(dcs_tx_code="244", dcs_rx_code=244))
        ]),
    ])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert entry["tx_tone"] == {"dcs": 244}
    assert entry["rx_tone"] == {"dcs": 244}


def test_narrow_bandwidth_threshold() -> None:
    codeplug = _codeplug([
        ("Mixed", [
            _channel("n", "NARROW", bandwidth_khz=12.5),
            _channel("w", "WIDE", bandwidth_khz=25.0),
            _channel("u", "UNSET"),
        ]),
    ])

    entries = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"]

    assert [e["bandwidth"] for e in entries] == ["NARROW", "WIDE", "WIDE"]


def test_extensions_control_scan_mute_talkaround_and_power() -> None:
    codeplug = _codeplug([
        ("APRS", [
            _channel(
                "aprs",
                "APRS",
                rx_frequency_mhz=144.390,
                extensions={"benlink": {
                    "scan": False, "mute": True, "talk_around": True, "power": "low",
                }},
            ),
            _channel("plain", "PLAIN"),
        ]),
    ])

    aprs, plain = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"]

    assert aprs["scan"] is False
    assert aprs["mute"] is True
    assert aprs["talk_around"] is True
    assert aprs["power"] == "low"
    # Defaults stay quiet rather than emitting every flag on every channel.
    assert plain["scan"] is True
    assert plain["power"] == "high"
    assert "mute" not in plain
    assert "talk_around" not in plain


def test_blank_unlisted_defaults_true_and_can_be_disabled() -> None:
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])

    assert benlink_plan_from_resolved(
        codeplug, capabilities=CAPABILITIES
    )["regions"][0]["blank_unlisted"] is True
    assert benlink_plan_from_resolved(
        codeplug, capabilities=CAPABILITIES, blank_unlisted=False
    )["regions"][0]["blank_unlisted"] is False


def test_too_many_zones_is_rejected() -> None:
    codeplug = _codeplug([
        (f"Z{i}", [_channel(f"c{i}", f"CH{i}")]) for i in range(7)
    ])

    with pytest.raises(ValueError, match="6 regions"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_too_many_channels_in_one_region_is_rejected() -> None:
    codeplug = _codeplug([
        ("Big", [_channel(f"c{i}", f"CH{i}") for i in range(33)]),
    ])

    with pytest.raises(ValueError, match="each region holds 32"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_channel_name_longer_than_the_radio_stores_is_rejected() -> None:
    codeplug = _codeplug([("Ham", [_channel("a", "ELEVENCHARS")])])

    with pytest.raises(ValueError, match="stores 10"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_channel_outside_every_zone_is_rejected() -> None:
    # There is no global channel pool on this radio, so an unzoned channel
    # cannot be written anywhere.
    orphan = _channel("orphan", "ORPHAN")
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])
    codeplug = ResolvedCodeplug(
        radio_id=codeplug.radio_id,
        radio_instance_id=codeplug.radio_instance_id,
        radio_instance=None,
        channels=codeplug.channels + (orphan,),
        zones=codeplug.zones,
    )

    with pytest.raises(ValueError, match="no global"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_dmr_mode_is_rejected() -> None:
    codeplug = _codeplug([("Digital", [_channel("a", "DMR1", mode="DMR")])])

    with pytest.raises(ValueError, match="unsupported"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_am_airband_receive_only_is_accepted() -> None:
    codeplug = _codeplug([
        ("Air", [
            _channel(
                "a", "ORD Twr",
                rx_frequency_mhz=126.9,
                tx_frequency_mhz=None,
                mode="AM",
                tx_permitted=False,
            )
        ]),
    ])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert entry["modulation"] == "AM"
    assert entry["tx_disable"] is True
    assert entry["rx_mhz"] == 126.9
    # Hardware-confirmed: this radio stores tx_freq as 0.0 for every AM
    # channel regardless of what is written, so the plan reflects that
    # rather than parking tx on rx like other receive-only channels do.
    assert entry["tx_mhz"] == 0.0


def test_am_with_transmit_permitted_is_rejected() -> None:
    # No Part 97 authority to transmit on aviation frequencies from an
    # amateur station; this is a legal constraint enforced regardless of
    # what the radio itself would accept.
    codeplug = _codeplug([
        ("Air", [_channel("a", "ORD Twr", rx_frequency_mhz=126.9, mode="AM", tx_permitted=True)]),
    ])

    with pytest.raises(ValueError, match="receive-only"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_fm_mode_omits_modulation_field() -> None:
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])

    entry = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)["regions"][0]["channels"][0]

    assert "modulation" not in entry


def test_mixed_ctcss_and_dcs_on_one_direction_is_rejected() -> None:
    codeplug = _codeplug([
        ("Ham", [
            _channel("a", "MIX", tones=ResolvedTones(ctcss_tx_hz=100.0, dcs_tx_code=244))
        ]),
    ])

    with pytest.raises(ValueError, match="both CTCSS and DCS"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_unknown_channel_reference_is_rejected() -> None:
    codeplug = ResolvedCodeplug(
        radio_id="vero_vrn76",
        radio_instance_id="n76_tan",
        radio_instance=None,
        channels=(),
        zones=(ResolvedZone(id="z", name="Ham", channel_references=("ghost",)),),
    )

    with pytest.raises(ValueError, match="unknown channel"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_json_output_is_valid_and_round_trips() -> None:
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])

    text = benlink_plan_json_from_resolved(
        codeplug, capabilities=CAPABILITIES, generated_by="codeplugger"
    )

    assert text.endswith("\n")
    plan = json.loads(text)
    assert plan["generated_by"] == "codeplugger"
    assert plan == benlink_plan_from_resolved(
        codeplug, capabilities=CAPABILITIES, generated_by="codeplugger"
    )


def test_plan_without_capabilities_still_builds() -> None:
    # Identity checking is lost, but a scratch plan should not require a
    # capabilities file to exist.
    codeplug = _codeplug([("Ham", [_channel("a", "2m Call")])])

    plan = benlink_plan_from_resolved(codeplug)

    assert plan["radio"] == {"model": "vero_vrn76"}
    assert plan["regions"][0]["channels"][0]["name"] == "2m Call"


def _codeplug_with_extensions(zones, extensions):
    codeplug = _codeplug(zones)
    return ResolvedCodeplug(
        radio_id=codeplug.radio_id,
        radio_instance_id=codeplug.radio_instance_id,
        radio_instance=None,
        channels=codeplug.channels,
        zones=codeplug.zones,
        extensions=extensions,
    )


def test_region_indices_pin_zones_around_a_reserved_region() -> None:
    # Region 1 is the radio's own APRS bank; a generated plan has to be able
    # to skip it rather than overwrite it.
    codeplug = _codeplug_with_extensions(
        [
            ("Family", [_channel("a", "Fam All")]),
            ("Ham", [_channel("b", "2m Call")]),
        ],
        {"benlink": {"region_indices": {"z1": 2}}},
    )

    plan = benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)

    assert [r["index"] for r in plan["regions"]] == [0, 2]
    assert [r["name"] for r in plan["regions"]] == ["Family", "Ham"]


def test_region_indices_reject_collisions() -> None:
    codeplug = _codeplug_with_extensions(
        [
            ("Family", [_channel("a", "Fam All")]),
            ("Ham", [_channel("b", "2m Call")]),
        ],
        {"benlink": {"region_indices": {"z1": 0}}},
    )

    with pytest.raises(ValueError, match="both map to region 0"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_region_indices_reject_out_of_range() -> None:
    codeplug = _codeplug_with_extensions(
        [("Family", [_channel("a", "Fam All")])],
        {"benlink": {"region_indices": {"z0": 6}}},
    )

    with pytest.raises(ValueError, match="outside the radio's 6 regions"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)


def test_region_indices_reject_unknown_zone() -> None:
    codeplug = _codeplug_with_extensions(
        [("Family", [_channel("a", "Fam All")])],
        {"benlink": {"region_indices": {"nope": 3}}},
    )

    with pytest.raises(ValueError, match="unknown zone"):
        benlink_plan_from_resolved(codeplug, capabilities=CAPABILITIES)
