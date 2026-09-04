"""Export resolved channels and zones into a p64tool TOML baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from tomlkit import aot, dumps, parse, table

from ..resolved import ResolvedCodeplug


def _required_array(document: object, key: str) -> list[object]:
    values = document.get(key) if hasattr(document, "get") else None
    if values is None:
        raise ValueError(f"p64tool baseline is missing [[{key}]] records")
    return list(values)


def _p64_tone(ctcss_hz: float | None, dcs_code: str | int | None) -> str | None:
    if ctcss_hz is not None:
        return f"{ctcss_hz:.1f}"
    if dcs_code is not None:
        code = str(dcs_code)
        if code.upper().startswith("D"):
            return code.upper()
        return f"D{code.zfill(3)}N"
    return None


def p64_toml_from_resolved(
    codeplug: ResolvedCodeplug,
    baseline_toml: str,
    *,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
    clear_unused_channels: bool = False,
    analog_bandwidth_khz: float | None = None,
    digital_contact_index: int | None = None,
    digital_rx_group_index: int | None = None,
) -> str:
    """Apply resolved channels/zones to a decoded p64tool TOML baseline.

    The baseline must come from ``p64tool decode``. Existing records are
    mutated in place so radio-wide settings and unsupported fields survive.
    """

    document = parse(baseline_toml)
    if fleet_instances is not None:
        _apply_fleet_identities(document, codeplug, fleet_instances)
    channel_records = _required_array(document, "channel")
    if len(codeplug.channels) > len(channel_records):
        raise ValueError(
            f"profile has {len(codeplug.channels)} channels but baseline has "
            f"{len(channel_records)} channel records"
        )
    for index, channel in enumerate(codeplug.channels):
        record = channel_records[index]
        record["index"] = index + 1
        record["name"] = channel.display_name
        record["mode"] = (
            "digital" if (channel.mode or "FM").upper() == "DMR" else "analog"
        )
        if channel.rx_frequency_mhz is not None:
            record["rx_mhz"] = channel.rx_frequency_mhz
        if channel.tx_frequency_mhz is not None:
            record["tx_mhz"] = channel.tx_frequency_mhz
        elif not channel.tx_permitted:
            record["rx_only"] = True
        bandwidth_khz = channel.bandwidth_khz
        if (channel.mode or "FM").upper() == "DMR":
            bandwidth_khz = bandwidth_khz or 12.5
        elif bandwidth_khz is None:
            bandwidth_khz = analog_bandwidth_khz
        if bandwidth_khz is not None:
            record["bandwidth_khz"] = bandwidth_khz
        if channel.power_w is not None:
            record["power"] = "high" if channel.power_w >= 0.5 else "low"
        if channel.color_code is not None:
            record["color_code"] = channel.color_code
        if channel.timeslot is not None:
            record["time_slot"] = channel.timeslot
        if (channel.mode or "FM").upper() == "DMR":
            if digital_contact_index is not None:
                record["contact"] = digital_contact_index
            if digital_rx_group_index is not None:
                record["rx_group"] = digital_rx_group_index
        rx_tone = _p64_tone(
            channel.tones.ctcss_rx_hz,
            channel.tones.dcs_rx_code,
        )
        tx_tone = _p64_tone(
            channel.tones.ctcss_tx_hz,
            channel.tones.dcs_tx_code,
        )
        if rx_tone is not None:
            record["rx_tone"] = rx_tone
        if tx_tone is not None:
            record["tx_tone"] = tx_tone
    if clear_unused_channels:
        for record in channel_records[len(codeplug.channels) :]:
            record["name"] = ""
            record["mode"] = "analog"
            record["rx_mhz"] = 0.0
            record["tx_mhz"] = 0.0
            record["rx_only"] = False

    zone_records = _required_array(document, "zone")
    if len(codeplug.zones) > len(zone_records):
        raise ValueError(
            f"profile has {len(codeplug.zones)} zones but baseline has "
            f"{len(zone_records)} zone records"
        )
    channel_by_reference = {
        channel.reference: index + 1
        for index, channel in enumerate(codeplug.channels)
    }
    for index, zone in enumerate(codeplug.zones):
        record = zone_records[index]
        record["index"] = index + 1
        record["name"] = zone.name
        record["channels"] = [
            channel_by_reference[reference]
            for reference in zone.channel_references
        ]
    if clear_unused_channels:
        for record in zone_records[len(codeplug.zones) :]:
            record["name"] = ""
            record["channels"] = []

    return dumps(document)


def _apply_fleet_identities(
    document: Any,
    codeplug: ResolvedCodeplug,
    fleet_instances: Mapping[str, Mapping[str, Any]],
) -> None:
    """Set the target ID and append every fleet radio as a private contact."""

    target = fleet_instances.get(codeplug.radio_instance_id)
    if target is None or target.get("dmr_id") is None:
        raise ValueError(
            f"fleet registry has no dmr_id for '{codeplug.radio_instance_id}'"
        )
    general = document.get("general")
    if general is None:
        raise ValueError("p64tool baseline is missing [general] settings")
    general["radio_dmr_id"] = target["dmr_id"]

    contact_records = document.get("contact")
    if contact_records is None:
        contact_records = aot()
        document["contact"] = contact_records
    existing_ids = {record.get("dmr_id") for record in contact_records}
    next_index = max((record.get("index", 0) for record in contact_records), default=0)
    for instance_id, instance in fleet_instances.items():
        dmr_id = instance.get("dmr_id")
        if dmr_id is None or dmr_id in existing_ids:
            continue
        next_index += 1
        contact = table()
        contact.update({
            "index": next_index,
            "name": instance.get("dmr_contact_name", str(dmr_id)),
            "dmr_id": dmr_id,
            "call_type": "private",
        })
        contact_records.append(contact)
        existing_ids.add(dmr_id)


def write_p64_toml(
    path: Path,
    codeplug: ResolvedCodeplug,
    baseline_path: Path,
    *,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
    clear_unused_channels: bool = False,
    analog_bandwidth_khz: float | None = None,
    digital_contact_index: int | None = None,
    digital_rx_group_index: int | None = None,
) -> None:
    """Write a p64tool TOML file derived from a decoded baseline."""

    baseline = baseline_path.read_text(encoding="utf-8")
    path.write_text(
        p64_toml_from_resolved(
            codeplug,
            baseline,
            fleet_instances=fleet_instances,
            clear_unused_channels=clear_unused_channels,
            analog_bandwidth_khz=analog_bandwidth_khz,
            digital_contact_index=digital_contact_index,
            digital_rx_group_index=digital_rx_group_index,
        ),
        encoding="utf-8",
    )