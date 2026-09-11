from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from codeplugger.exporters.qdmr_yaml import (
    PLACEHOLDER_TALKGROUP_NUMBER,
    qdmr_yaml_from_resolved,
    write_qdmr_yaml,
)
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedContact,
    ResolvedRxGroup,
    ResolvedScanList,
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


def _codeplug(
    channels: tuple[ResolvedChannel, ...],
    *,
    radio_instance: dict | None = None,
    **extra,
) -> ResolvedCodeplug:
    return ResolvedCodeplug(
        radio_id="baofeng_dm32",
        radio_instance_id="dm32_01",
        radio_instance=radio_instance,
        channels=channels,
        zones=(
            ResolvedZone(
                "z1", "Zone 1", tuple(ch.reference for ch in channels)
            ),
        ),
        **extra,
    )


def test_fm_channel_fields_and_zone_references() -> None:
    codeplug = _codeplug(
        (
            _channel(tones=ResolvedTones(ctcss_tx_hz=67.0)),
            _channel(
                reference="a2",
                assignment_id="a2",
                display_name="WIDE",
                rx_frequency_mhz=146.52,
                tx_frequency_mhz=146.52,
                bandwidth_khz=25.0,
                power_w=5.0,
            ),
        )
    )

    document = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))

    first = document["channels"][0]["fm"]
    assert first["name"] == "SIMPLEX"
    assert first["rxFrequency"] == "446 MHz"
    assert first["bandwidth"] == "Narrow"
    assert first["power"] == "Low"
    assert first["txTone"] == {"ctcss": "67.0 Hz"}
    assert "rxTone" not in first
    second = document["channels"][1]["fm"]
    assert second["bandwidth"] == "Wide"
    assert second["power"] == "High"
    assert document["zones"][0]["A"] == ["ch1", "ch2"]
    assert document["zones"][0]["B"] == []
    # DM32UV structurally requires one radio ID even with no DMR channels.
    assert document["radioIDs"] == [
        {"dmr": {"id": "id1", "name": "UNUSED", "number": 1}}
    ]
    assert document["settings"]["defaultID"] == "id1"


def test_rx_only_channel_reuses_rx_frequency() -> None:
    codeplug = _codeplug(
        (_channel(tx_frequency_mhz=None, tx_permitted=False),)
    )

    record = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))["channels"][0][
        "fm"
    ]

    assert record["rxOnly"] is True
    assert record["txFrequency"] == record["rxFrequency"]


def test_am_channel_uses_qdmr_am_variant() -> None:
    channel = _channel(
        mode="AM",
        display_name="ORD APP",
        rx_frequency_mhz=119.0,
        tx_frequency_mhz=None,
        tx_permitted=False,
    )

    document = yaml.safe_load(qdmr_yaml_from_resolved(_codeplug((channel,))))

    record = document["channels"][0]
    assert "am" in record
    assert record["am"]["rxFrequency"] == "119 MHz"
    assert record["am"]["rxOnly"] is True


def test_dcs_tone_normalization() -> None:
    codeplug = _codeplug(
        (
            _channel(
                tones=ResolvedTones(dcs_tx_code="D023N", dcs_rx_code=754)
            ),
        )
    )

    record = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))["channels"][0][
        "fm"
    ]

    assert record["txTone"] == {"dcs": "n023"}
    assert record["rxTone"] == {"dcs": "n754"}


def test_inverted_dcs_code() -> None:
    codeplug = _codeplug((_channel(tones=ResolvedTones(dcs_tx_code="D023I")),))

    record = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))["channels"][0][
        "fm"
    ]

    assert record["txTone"] == {"dcs": "i023"}


def test_dmr_channel_requires_identity_and_maps_fields() -> None:
    dmr = _channel(
        reference="d1",
        assignment_id="d1",
        display_name="RPT",
        rx_frequency_mhz=442.5,
        tx_frequency_mhz=447.5,
        mode="DMR",
        power_w=5.0,
        color_code=1,
        timeslot=2,
    )
    codeplug = _codeplug(
        (dmr,), radio_instance={"dmr_id": 1234567, "dmr_contact_name": "K9ABC"}
    )

    document = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))

    record = document["channels"][0]["dmr"]
    assert record["admit"] == "ColorCode"
    assert record["colorCode"] == 1
    assert record["timeSlot"] == "TS2"
    assert document["radioIDs"] == [
        {"dmr": {"id": "id1", "name": "K9ABC", "number": 1234567}}
    ]
    assert document["settings"]["defaultID"] == "id1"


def test_dmr_timeslot_falls_back_to_single_authorized_slot() -> None:
    dmr = _channel(
        mode="DMR", color_code=5, timeslot=None, timeslots=(1,)
    )
    codeplug = _codeplug((dmr,), radio_instance={"dmr_id": 1})

    record = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))["channels"][0][
        "dmr"
    ]

    assert record["timeSlot"] == "TS1"


def test_placeholder_talkgroup_and_fleet_private_contacts() -> None:
    codeplug = _codeplug((_channel(),))

    document = yaml.safe_load(
        qdmr_yaml_from_resolved(
            codeplug,
            fleet_instances={
                "dm32_01": {"dmr_id": 1234567, "dmr_contact_name": "K9ABC"},
                "dm32_02": {"dmr_id": 1234568},
                "uv5r_01": {},
            },
        )
    )

    contacts = [entry["dmr"] for entry in document["contacts"]]
    assert [c["name"] for c in contacts] == ["K9ABC", "1234568", "UNUSED TG99"]
    assert contacts[0]["type"] == "PrivateCall"
    assert contacts[2]["type"] == "GroupCall"
    assert contacts[2]["number"] == PLACEHOLDER_TALKGROUP_NUMBER
    assert document["groupLists"] == [
        {"id": "grp1", "name": "UNUSED", "contacts": [contacts[2]["id"]]}
    ]


def test_placeholder_contact_present_without_fleet_registry() -> None:
    document = yaml.safe_load(qdmr_yaml_from_resolved(_codeplug((_channel(),))))

    assert len(document["contacts"]) == 1
    assert len(document["groupLists"]) == 1


def test_contacts_group_lists_and_scan_lists_from_profile() -> None:
    dmr = _channel(
        reference="d1",
        assignment_id="d1",
        display_name="RPT",
        rx_frequency_mhz=442.5,
        tx_frequency_mhz=447.5,
        mode="DMR",
        color_code=1,
        timeslot=2,
        contact_id="tg_local",
        rx_group_id="grp_local",
        scan_list_id="city",
    )
    fm = _channel(scan_list_id="city")
    codeplug = _codeplug(
        (dmr, fm),
        radio_instance={"dmr_id": 1234567},
        contacts=(
            ResolvedContact("tg_local", "Local", 9, "group"),
            ResolvedContact("tg_state", "Statewide", 3117, "group"),
        ),
        rx_groups=(
            ResolvedRxGroup("grp_local", "Local", ("tg_local", "tg_state")),
        ),
        scan_lists=(ResolvedScanList("city", "City", ("d1", "a1")),),
    )

    document = yaml.safe_load(
        qdmr_yaml_from_resolved(
            codeplug,
            fleet_instances={"dm32_01": {"dmr_id": 1234567}},
        )
    )

    contacts = [entry["dmr"] for entry in document["contacts"]]
    assert [(c["name"], c["type"], c["number"]) for c in contacts] == [
        ("Local", "GroupCall", 9),
        ("Statewide", "GroupCall", 3117),
        ("1234567", "PrivateCall", 1234567),
    ]
    assert document["groupLists"] == [
        {"id": "grp1", "name": "Local", "contacts": ["cont1", "cont2"]}
    ]
    record = document["channels"][0]["dmr"]
    assert record["groupList"] == "grp1"
    assert record["contact"] == "cont1"
    assert record["scanList"] == "scan1"
    assert document["channels"][1]["fm"]["scanList"] == "scan1"
    assert document["scanLists"] == [
        {"id": "scan1", "name": "City", "channels": ["ch1", "ch2"]}
    ]
    # Real group policy defined: no UNUSED placeholder needed.
    assert all(c["number"] != PLACEHOLDER_TALKGROUP_NUMBER for c in contacts)

def test_qdmr_extensions_add_settings_and_aprs_configuration() -> None:
    dmr = _channel(
        mode="DMR",
        color_code=1,
        timeslot=1,
        extensions={"qdmr": {"aprs": "aprs1"}},
    )
    codeplug = _codeplug(
        (dmr,),
        radio_instance={"dmr_id": 1234567},
        extensions={
            "qdmr": {
                "settings": {
                    "introLine1": "K9ABC",
                    "boot": {"display": "Text"},
                    "audio": {"fmMicGain": 2, "voxDelay": "500 ms"},
                    "dmr": {"groupCallMatch": False},
                    "gnss": {"systems": ["GPS"]},
                },
                "contacts": [
                    {
                        "dmr": {
                            "id": "aprs_contact",
                            "name": "DMR APRS",
                            "ring": False,
                            "type": "PrivateCall",
                            "number": 310999,
                        }
                    }
                ],
                "positioning": [
                    {
                        "dmr": {
                            "id": "aprs1",
                            "name": "DMR APRS",
                            "period": "5 min",
                            "contact": "aprs_contact",
                        }
                    }
                ],
            }
        },
    )

    document = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))

    assert document["settings"]["introLine1"] == "K9ABC"
    assert document["settings"]["boot"] == {"display": "Text"}
    assert document["settings"]["audio"] == {
        "fmMicGain": 2,
        "voxDelay": "500 ms",
    }
    assert document["settings"]["dmr"] == {"groupCallMatch": False}
    assert document["settings"]["gnss"] == {"systems": ["GPS"]}
    assert document["contacts"][-1]["dmr"]["id"] == "aprs_contact"
    assert document["positioning"][0]["dmr"]["id"] == "aprs1"
    assert document["channels"][0]["dmr"]["aprs"] == "aprs1"


def test_qdmr_channel_extension_cannot_override_generated_fields() -> None:
    channel = _channel(
        extensions={"qdmr": {"name": "OVERRIDE", "rxOnly": True}}
    )

    with pytest.raises(ValueError, match="generated channel fields"):
        qdmr_yaml_from_resolved(_codeplug((channel,)))


def test_dmr_channel_without_color_code_fails() -> None:
    dmr = _channel(mode="DMR", timeslot=1)
    codeplug = _codeplug((dmr,), radio_instance={"dmr_id": 1})

    with pytest.raises(ValueError, match="color code"):
        qdmr_yaml_from_resolved(codeplug)


def test_dmr_channel_without_usable_timeslot_fails() -> None:
    dmr = _channel(mode="DMR", color_code=1, timeslot=None, timeslots=(1, 2))
    codeplug = _codeplug((dmr,), radio_instance={"dmr_id": 1})

    with pytest.raises(ValueError, match="timeslot"):
        qdmr_yaml_from_resolved(codeplug)


def test_multi_identity_profile_emits_one_radio_id_per_used_key() -> None:
    """Issue #20: dmr_id bound per zone/assignment -> one radioIDs entry each."""

    ham = _channel(
        reference="a1",
        assignment_id="a1",
        mode="DMR",
        color_code=1,
        timeslot=1,
        dmr_id_key="ham",
        dmr_id=1234567,
    )
    family = _channel(
        reference="a2",
        assignment_id="a2",
        mode="DMR",
        color_code=1,
        timeslot=2,
        dmr_id_key="family",
        dmr_id=123,
    )
    codeplug = _codeplug(
        (ham, family),
        radio_instance={
            "dmr_ids": [
                {"key": "ham", "id": 1234567, "name": "CALLSIGN"},
                {"key": "family", "id": 123, "name": "Family"},
            ],
            "default_dmr_id": "ham",
        },
    )

    document = yaml.safe_load(qdmr_yaml_from_resolved(codeplug))

    assert document["radioIDs"] == [
        {"dmr": {"id": "id1", "name": "CALLSIGN", "number": 1234567}},
        {"dmr": {"id": "id2", "name": "Family", "number": 123}},
    ]
    assert document["channels"][0]["dmr"]["radioId"] == "id1"
    assert document["channels"][1]["dmr"]["radioId"] == "id2"
    # instance default_dmr_id ("ham") drives qdmr's defaultID.
    assert document["settings"]["defaultID"] == "id1"


def test_dmr_channel_without_dmr_id_fails() -> None:
    dmr = _channel(mode="DMR", color_code=1, timeslot=1)

    with pytest.raises(ValueError, match="dmr_id"):
        qdmr_yaml_from_resolved(_codeplug((dmr,)))


def test_missing_analog_bandwidth_fails_without_default() -> None:
    codeplug = _codeplug((_channel(bandwidth_khz=None),))

    with pytest.raises(ValueError, match="bandwidth"):
        qdmr_yaml_from_resolved(codeplug)

    document = yaml.safe_load(
        qdmr_yaml_from_resolved(codeplug, analog_bandwidth_khz=12.5)
    )
    assert document["channels"][0]["fm"]["bandwidth"] == "Narrow"


def test_unrepresentable_bandwidth_fails() -> None:
    codeplug = _codeplug((_channel(bandwidth_khz=20.0),))

    with pytest.raises(ValueError, match="not representable"):
        qdmr_yaml_from_resolved(codeplug)


def test_mixed_ctcss_and_dcs_on_one_side_fails() -> None:
    codeplug = _codeplug(
        (_channel(tones=ResolvedTones(ctcss_tx_hz=67.0, dcs_tx_code=23)),)
    )

    with pytest.raises(ValueError, match="mixes CTCSS and DCS"):
        qdmr_yaml_from_resolved(codeplug)


@pytest.mark.skipif(
    shutil.which("dmrconf") is None, reason="dmrconf is not installed"
)
def test_dmrconf_verify_accepts_generated_codeplug(tmp_path) -> None:
    channels = (
        _channel(tones=ResolvedTones(ctcss_tx_hz=67.0), scan_list_id="city"),
        _channel(
            reference="d1",
            assignment_id="d1",
            display_name="RPT",
            rx_frequency_mhz=442.5,
            tx_frequency_mhz=447.5,
            mode="DMR",
            power_w=5.0,
            color_code=1,
            timeslot=2,
            contact_id="tg_local",
            rx_group_id="grp_local",
            scan_list_id="city",
            extensions={"qdmr": {"aprs": "aprs1"}},
        ),
    )
    codeplug = _codeplug(
        (channels),
        radio_instance={"dmr_id": 1234567},
        contacts=(ResolvedContact("tg_local", "Local", 9, "group"),),
        rx_groups=(ResolvedRxGroup("grp_local", "Local", ("tg_local",)),),
        scan_lists=(ResolvedScanList("city", "City", ("a1", "d1")),),
        extensions={
            "qdmr": {
                "settings": {
                    "boot": {"display": "Text"},
                    "audio": {"fmMicGain": 2, "voxDelay": "500 ms"},
                    "dmr": {"groupCallMatch": False},
                    "gnss": {"systems": ["GPS"]},
                },
                "contacts": [
                    {
                        "dmr": {
                            "id": "aprs_contact",
                            "name": "DMR APRS",
                            "ring": False,
                            "type": "PrivateCall",
                            "number": 310999,
                        }
                    }
                ],
                "positioning": [
                    {
                        "dmr": {
                            "id": "aprs1",
                            "name": "DMR APRS",
                            "period": "5 min",
                            "contact": "aprs_contact",
                        }
                    }
                ],
            }
        },
    )
    output = tmp_path / "codeplug.yaml"
    write_qdmr_yaml(output, codeplug)

    result = subprocess.run(
        ["dmrconf", "verify", "--radio=dm32uv", str(output)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    shutil.which("dmrconf") is None, reason="dmrconf is not installed"
)
def test_dmrconf_verify_accepts_analog_only_codeplug(tmp_path) -> None:
    codeplug = _codeplug((_channel(tones=ResolvedTones(ctcss_tx_hz=67.0)),))
    output = tmp_path / "codeplug.yaml"
    write_qdmr_yaml(output, codeplug)

    result = subprocess.run(
        ["dmrconf", "verify", "--radio=dm32uv", str(output)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
