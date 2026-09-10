"""Export resolved codeplugs as qdmr extensible-codeplug YAML.

The output targets ``dmrconf`` (qdmr's CLI): ``dmrconf verify --radio=dm32uv``
validates a generated file against radio limits without hardware, and
``dmrconf write`` programs the radio. Field names and value syntax were
verified against qdmr @ ``e84d4b3a`` (v0.15.1 + DM32UV DCS fix #990):

- Channels are ``{fm: {...}}`` or ``{dmr: {...}}`` maps; zones reference
  channel ids in ``A``/``B`` lists (``lib/zone.cc``).
- Tones serialize as ``{ctcss: "67.0 Hz"}`` or ``{dcs: "n023"}`` where the
  prefix is ``n`` (normal) or ``i`` (inverted) and the digits are the
  standard octal DCS code (``SelectiveCall::format`` in ``lib/signaling.cc``).
- Channel power is one of ``Min``/``Low``/``Mid``/``High``/``Max``
  (``Channel::Power`` in ``lib/channel.hh``).

Every exporter default below is named and deliberate; none are copied
blindly from a CPS implementation (see docs/plan.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from ..resolved import ResolvedChannel, ResolvedCodeplug

# qdmr config format version this exporter was verified against.
QDMR_CONFIG_VERSION = "0.15.1"

# Radio-wide settings. micLevel/squelch/vox/tot match qdmr's own defaults
# (ConfigItem defaults); they are operator preferences, not RF facts.
DEFAULT_SETTINGS: dict[str, Any] = {
    "introLine1": "",
    "introLine2": "",
    "micLevel": 3,
    "speech": False,
    "power": "High",
    "squelch": 1,
    "vox": 0,
    "tot": 0,
}

# Used when a channel has no resolved power_w. High is the OEM CPS default
# and errs toward making contact rather than silently under-powering.
DEFAULT_POWER = "High"

# FM admit criterion: transmit regardless of channel state, the OEM default.
DEFAULT_FM_ADMIT = "Always"

# DMR admit criterion: require matching color code, standard repeater practice.
DEFAULT_DMR_ADMIT = "ColorCode"

# Watts at or below these map to qdmr's Low/Mid steps; above is High.
POWER_LOW_MAX_W = 1.0
POWER_MID_MAX_W = 2.5

# The DM-32UV encoder unconditionally requires at least one group-call
# contact and one RX group list, even when no channel references them
# (qdmr lib/dm32uv_limits.cc: RadioLimitList minimums of 1, and
# RadioLimitGroupCallRefList(1, 32)). This placeholder satisfies that
# structural minimum without inventing talkgroup policy: no channel
# references it, and TG99 is the conventional DMR simplex talkgroup, so
# accidental manual selection stays harmless.
PLACEHOLDER_TALKGROUP_NAME = "UNUSED TG99"
PLACEHOLDER_TALKGROUP_NUMBER = 99
PLACEHOLDER_GROUP_LIST_NAME = "UNUSED"

# Resolved contact kinds mapped to qdmr DMR contact types.
CONTACT_TYPES = {
    "group": "GroupCall",
    "private": "PrivateCall",
    "all": "AllCall",
}


def _frequency(value_mhz: float) -> str:
    text = f"{value_mhz:.6f}".rstrip("0").rstrip(".")
    return f"{text} MHz"


def _power(power_w: float | None) -> str:
    if power_w is None:
        return DEFAULT_POWER
    if power_w <= POWER_LOW_MAX_W:
        return "Low"
    if power_w <= POWER_MID_MAX_W:
        return "Mid"
    return "High"


def _dcs(code: str | int, *, channel_name: str) -> str:
    text = str(code).strip().upper()
    inverted = False
    if text.startswith("D"):
        text = text[1:]
    if text.endswith(("N", "I")):
        inverted = text.endswith("I")
        text = text[:-1]
    if not text.isdigit():
        raise ValueError(
            f"channel '{channel_name}' has unsupported DCS code {code!r}"
        )
    return f"{'i' if inverted else 'n'}{int(text):03d}"


def _tone(
    ctcss_hz: float | None, dcs_code: str | int | None, *, channel_name: str
) -> dict[str, str] | None:
    if ctcss_hz is not None and dcs_code is not None:
        raise ValueError(
            f"channel '{channel_name}' mixes CTCSS and DCS on one side, "
            "which qdmr's SelectiveCall cannot represent"
        )
    if ctcss_hz is not None:
        return {"ctcss": f"{ctcss_hz:.1f} Hz"}
    if dcs_code is not None:
        return {"dcs": _dcs(dcs_code, channel_name=channel_name)}
    return None


def _frequencies(channel: ResolvedChannel) -> dict[str, Any]:
    rx = channel.rx_frequency_mhz
    tx = channel.tx_frequency_mhz
    rx_only = not channel.tx_permitted or tx is None
    return {
        "rxFrequency": _frequency(rx),
        # qdmr requires a TX frequency even for rxOnly channels; reuse RX so
        # no transmittable frequency is invented.
        "txFrequency": _frequency(tx if tx is not None else rx),
        "rxOnly": rx_only,
    }


def _fm_channel(
    channel: ResolvedChannel,
    channel_id: str,
    analog_bandwidth_khz: float | None,
) -> dict[str, Any]:
    bandwidth_khz = channel.bandwidth_khz or analog_bandwidth_khz
    if bandwidth_khz is None:
        raise ValueError(
            f"channel '{channel.display_name}' has no bandwidth and no "
            "analog_bandwidth_khz default was provided"
        )
    if bandwidth_khz not in (12.5, 25.0):
        raise ValueError(
            f"channel '{channel.display_name}' bandwidth {bandwidth_khz} kHz "
            "is not representable in qdmr (Narrow=12.5, Wide=25)"
        )
    record: dict[str, Any] = {
        "id": channel_id,
        "name": channel.display_name,
        **_frequencies(channel),
        "bandwidth": "Narrow" if bandwidth_khz == 12.5 else "Wide",
        "admit": DEFAULT_FM_ADMIT,
        "power": _power(channel.power_w),
    }
    rx_tone = _tone(
        channel.tones.ctcss_rx_hz,
        channel.tones.dcs_rx_code,
        channel_name=channel.display_name,
    )
    tx_tone = _tone(
        channel.tones.ctcss_tx_hz,
        channel.tones.dcs_tx_code,
        channel_name=channel.display_name,
    )
    if rx_tone is not None:
        record["rxTone"] = rx_tone
    if tx_tone is not None:
        record["txTone"] = tx_tone
    return {"fm": record}


def _am_channel(channel: ResolvedChannel, channel_id: str) -> dict[str, Any]:
    record = {
        "id": channel_id,
        "name": channel.display_name,
        **_frequencies(channel),
        "power": _power(channel.power_w),
    }
    return {"am": record}


def _dmr_channel(
    channel: ResolvedChannel,
    channel_id: str,
    contact_ids: Mapping[str, str],
    rx_group_ids: Mapping[str, str],
) -> dict[str, Any]:
    if channel.color_code is None:
        raise ValueError(
            f"DMR channel '{channel.display_name}' has no color code; color "
            "codes are RF facts and must come from SSRF data, not a default"
        )
    timeslot = channel.timeslot
    if timeslot is None and len(channel.timeslots) == 1:
        timeslot = channel.timeslots[0]
    if timeslot not in (1, 2):
        raise ValueError(
            f"DMR channel '{channel.display_name}' has no usable timeslot; "
            "timeslots are RF facts and must come from SSRF data"
        )
    return {
        "dmr": {
            "id": channel_id,
            "name": channel.display_name,
            **_frequencies(channel),
            "admit": DEFAULT_DMR_ADMIT,
            "colorCode": channel.color_code,
            "timeSlot": f"TS{timeslot}",
            **(
                {"groupList": rx_group_ids[channel.rx_group_id]}
                if channel.rx_group_id is not None
                else {}
            ),
            **(
                {"contact": contact_ids[channel.contact_id]}
                if channel.contact_id is not None
                else {}
            ),
            "power": _power(channel.power_w),
        }
    }


def _contacts(
    codeplug: ResolvedCodeplug,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, str],
]:
    """Return (contacts, groupLists, contact id map, RX group id map).

    Profile-selected talkgroups come first; fleet-registry members become
    private contacts, mirroring the P4 exporter's contact policy. When the
    profile defines no RX groups, the DM-32UV structural minimums (\u22651
    group-call contact, \u22651 RX group list) are satisfied by the UNUSED
    placeholder, exactly as before.
    """

    contacts: list[dict[str, Any]] = []
    contact_ids: dict[str, str] = {}
    for contact in codeplug.contacts:
        yaml_id = f"cont{len(contacts) + 1}"
        contact_ids[contact.id] = yaml_id
        contacts.append(
            {
                "dmr": {
                    "id": yaml_id,
                    "name": contact.name,
                    "ring": False,
                    "type": CONTACT_TYPES[contact.kind],
                    "number": contact.number,
                }
            }
        )
    for instance in (fleet_instances or {}).values():
        dmr_id = instance.get("dmr_id")
        if dmr_id is None:
            continue
        contacts.append(
            {
                "dmr": {
                    "id": f"cont{len(contacts) + 1}",
                    "name": str(instance.get("dmr_contact_name", dmr_id)),
                    "ring": False,
                    "type": "PrivateCall",
                    "number": int(dmr_id),
                }
            }
        )

    group_lists: list[dict[str, Any]] = []
    rx_group_ids: dict[str, str] = {}
    for group in codeplug.rx_groups:
        yaml_id = f"grp{len(group_lists) + 1}"
        rx_group_ids[group.id] = yaml_id
        group_lists.append(
            {
                "id": yaml_id,
                "name": group.name,
                "contacts": [
                    contact_ids[contact_id] for contact_id in group.contact_ids
                ],
            }
        )
    if not group_lists:
        placeholder_id = f"cont{len(contacts) + 1}"
        contacts.append(
            {
                "dmr": {
                    "id": placeholder_id,
                    "name": PLACEHOLDER_TALKGROUP_NAME,
                    "ring": False,
                    "type": "GroupCall",
                    "number": PLACEHOLDER_TALKGROUP_NUMBER,
                }
            }
        )
        group_lists.append(
            {
                "id": "grp1",
                "name": PLACEHOLDER_GROUP_LIST_NAME,
                "contacts": [placeholder_id],
            }
        )
    return contacts, group_lists, contact_ids, rx_group_ids


def qdmr_yaml_from_resolved(
    codeplug: ResolvedCodeplug,
    *,
    analog_bandwidth_khz: float | None = None,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Return a qdmr extensible-codeplug YAML document.

    DMR channels require a radio identity: the resolved instance metadata
    must carry ``dmr_id`` (from the instance registry).
    """

    channels: list[dict[str, Any]] = []
    channel_ids: dict[str, str] = {}
    contacts, group_lists, contact_ids, rx_group_ids = _contacts(
        codeplug, fleet_instances
    )
    scan_list_ids = {
        scan_list.id: f"scan{index + 1}"
        for index, scan_list in enumerate(codeplug.scan_lists)
    }
    has_dmr = False
    for index, channel in enumerate(codeplug.channels):
        channel_id = f"ch{index + 1}"
        channel_ids[channel.reference] = channel_id
        mode = (channel.mode or "FM").upper()
        if mode == "DMR":
            has_dmr = True
            record = _dmr_channel(channel, channel_id, contact_ids, rx_group_ids)
        elif mode == "AM":
            record = _am_channel(channel, channel_id)
        else:
            record = _fm_channel(channel, channel_id, analog_bandwidth_khz)
        if channel.scan_list_id is not None:
            next(iter(record.values()))["scanList"] = scan_list_ids[
                channel.scan_list_id
            ]
        channels.append(record)

    zones = [
        {
            "id": f"zone{index + 1}",
            "name": zone.name,
            "A": [channel_ids[ref] for ref in zone.channel_references],
            "B": [],
        }
        for index, zone in enumerate(codeplug.zones)
    ]

    document: dict[str, Any] = {
        "version": QDMR_CONFIG_VERSION,
        "settings": dict(DEFAULT_SETTINGS),
        "radioIDs": [],
        "contacts": contacts,
        "groupLists": group_lists,
        "channels": channels,
        "zones": zones,
    }
    if codeplug.scan_lists:
        document["scanLists"] = [
            {
                "id": scan_list_ids[scan_list.id],
                "name": scan_list.name,
                "channels": [
                    channel_ids[ref] for ref in scan_list.channel_references
                ],
            }
            for scan_list in codeplug.scan_lists
        ]

    metadata = codeplug.radio_instance or {}
    dmr_id = metadata.get("dmr_id")
    if dmr_id is not None:
        name = str(metadata.get("dmr_contact_name", codeplug.radio_instance_id))
        document["radioIDs"] = [
            {"dmr": {"id": "id1", "name": name, "number": int(dmr_id)}}
        ]
        document["settings"]["defaultID"] = "id1"
    elif has_dmr:
        raise ValueError(
            f"instance '{codeplug.radio_instance_id}' has DMR channels but "
            "no dmr_id in its registry metadata"
        )

    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def write_qdmr_yaml(
    path: Path,
    codeplug: ResolvedCodeplug,
    *,
    analog_bandwidth_khz: float | None = None,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Write qdmr YAML to ``path``."""

    path.write_text(
        qdmr_yaml_from_resolved(
            codeplug,
            analog_bandwidth_khz=analog_bandwidth_khz,
            fleet_instances=fleet_instances,
        ),
        encoding="utf-8",
    )
