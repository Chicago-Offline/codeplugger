"""Export a resolved codeplug as a ``benlink-codeplug`` plan for Benshi radios.

This is the write path for the Vero VR-N76 and its Benshi-protocol siblings.
Unlike every other exporter here, the artifact is not a file a vendor CPS
imports -- there is no CPS for these radios on desktop, and no CHIRP driver.
The plan is consumed by ``apply_codeplug.py`` in the benlink fork, which
writes channels over BLE/RFCOMM directly.

The format is specified in the benlink repo (``docs/codeplug-plan.md``); the
subset this exporter emits is version 1.

Three things make this radio family different from the CHIRP/qdmr targets:

**Zones are regions, and regions are the only addressing.** Channel IDs
restart at 0 inside every region, so there is no global channel pool a zone
can point into. A codeplugger zone therefore becomes a region, and a channel
that no zone references has nowhere to live at all. That is an error here
rather than a silently dropped row, which is what a flat exporter would do.

**Channel slots are the channel's identity.** A slot is a physical memory,
selected by number on the radio's dial, so slot order is the operator-visible
order. Zone order and assignment order are preserved exactly; the exporter
never sorts.

**Transmit inhibit is real.** CHIRP CSV cannot express receive-only, so
``chirp_csv`` exports those as ordinary simplex channels and leaves the intent
in the name. The Benshi wire format has a ``tx_disable`` bit, so
``tx_permitted: false`` is honoured properly and weather/airband/public-safety
channels come out genuinely unable to transmit.

Radio-specific switches live under a per-assignment ``extensions.benlink``
key: ``scan``, ``mute``, ``talk_around``, ``power``. Scan is an extension
because this radio has a single per-channel scan bit rather than the named
scan lists modelled in ``scan_lists`` -- there is nothing to map them onto,
and guessing membership from a scan list would quietly change what the
operator hears.

Zones land in regions in profile order by default. A profile-level
``extensions.benlink.region_indices`` mapping overrides that per zone, which
matters because regions the plan does not name are left untouched: the N76
manages its own APRS region, and pinning the generated zones around it is the
only way to keep a generated codeplug from walking over a region the radio
firmware owns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..resolved import ResolvedChannel, ResolvedCodeplug

PLAN_FORMAT = "benlink-codeplug"
PLAN_VERSION = 1

# 12.5 kHz and below is narrow; the radio has one bit, not a bandwidth field.
NARROW_BANDWIDTH_KHZ = 12.5

# RfCh.name_str is bf_str(10) on the wire. Longer names are not truncated
# here: silently shortening a name produces a radio whose display disagrees
# with the printed reference sheet, and the operator finds out mid-net.
MAX_NAME_CHARS = 10

VALID_POWER = ("high", "med", "low")


def _channel_extensions(channel: ResolvedChannel) -> dict[str, Any]:
    extension = channel.extensions.get("benlink")
    if extension is None:
        return {}
    if not isinstance(extension, dict):
        raise ValueError(
            f"channel '{channel.display_name}' has a non-mapping "
            "extensions.benlink value"
        )
    return extension


def _tone(tx_hz: float | None, rx_hz: float | None,
          dcs_tx: str | int | None, dcs_rx: str | int | None,
          direction: str, channel: ResolvedChannel) -> Any:
    """Return one direction's tone in plan form.

    The radio stores a single sub-audio value per direction, so a channel
    cannot carry CTCSS one way and DCS the other in a way this exporter
    would know how to reconcile. Mixing is rejected for the same reason the
    CHIRP exporter rejects it.
    """
    ctcss = tx_hz if direction == "tx" else rx_hz
    dcs = dcs_tx if direction == "tx" else dcs_rx
    if ctcss is not None and dcs is not None:
        raise ValueError(
            f"channel '{channel.display_name}' sets both CTCSS and DCS on "
            f"{direction}, which the Benshi sub-audio field cannot represent"
        )
    if ctcss is not None:
        return {"ctcss": round(float(ctcss), 1)}
    if dcs is not None:
        return {"dcs": int(dcs)}
    return None


def _channel_entry(slot: int, channel: ResolvedChannel) -> dict[str, Any]:
    mode = (channel.mode or "FM").upper()
    if mode != "FM":
        raise ValueError(
            f"channel '{channel.display_name}' mode '{mode}' is unsupported: "
            "the VR-N76 reports no DMR support and no AM channel has been "
            "verified on it"
        )

    name = channel.display_name
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(
            f"channel name '{name}' is {len(name)} characters; the radio "
            f"stores {MAX_NAME_CHARS}. Shorten it in the profile rather than "
            "letting the radio truncate it."
        )

    extension = _channel_extensions(channel)

    power = str(extension.get("power", "high")).lower()
    if power not in VALID_POWER:
        raise ValueError(
            f"channel '{name}' has extensions.benlink.power {power!r}; "
            f"expected one of {', '.join(VALID_POWER)}"
        )

    tones = channel.tones
    tx_tone = _tone(tones.ctcss_tx_hz, tones.ctcss_rx_hz,
                    tones.dcs_tx_code, tones.dcs_rx_code, "tx", channel)
    rx_tone = _tone(tones.ctcss_tx_hz, tones.ctcss_rx_hz,
                    tones.dcs_tx_code, tones.dcs_rx_code, "rx", channel)

    # A receive-only channel still needs a transmit frequency on the wire;
    # the radio simply refuses to key. Parking tx on rx keeps the plan
    # readable and means an accidental tx_disable flip transmits simplex on
    # a frequency the operator is already listening to, not somewhere else.
    tx_permitted = channel.tx_permitted and channel.tx_frequency_mhz is not None
    tx_mhz = channel.tx_frequency_mhz if tx_permitted else channel.rx_frequency_mhz

    entry: dict[str, Any] = {
        "slot": slot,
        "name": name,
        "rx_mhz": round(float(channel.rx_frequency_mhz), 6),
        "tx_mhz": round(float(tx_mhz), 6),
        "bandwidth": (
            "NARROW"
            if channel.bandwidth_khz is not None
            and channel.bandwidth_khz <= NARROW_BANDWIDTH_KHZ
            else "WIDE"
        ),
        "power": power,
        "scan": bool(extension.get("scan", True)),
        "tx_disable": not tx_permitted,
    }
    if tx_tone is not None:
        entry["tx_tone"] = tx_tone
    if rx_tone is not None:
        entry["rx_tone"] = rx_tone
    if extension.get("mute"):
        entry["mute"] = True
    if extension.get("talk_around"):
        entry["talk_around"] = True
    if channel.notes:
        entry["comment"] = channel.notes
    return entry


def _region_indices(codeplug: ResolvedCodeplug, max_zones: Any) -> dict[str, int]:
    """Return the explicit zone-id to region-index map, validated.

    Zones without an entry fall back to their profile position. Collisions
    are rejected: two zones in one region would silently overwrite each
    other, and the second write would look perfectly successful.
    """
    extension = codeplug.extensions.get("benlink") or {}
    mapping = extension.get("region_indices") or {}
    if not isinstance(mapping, dict):
        raise ValueError("extensions.benlink.region_indices must be a mapping")

    zone_ids = {zone.id for zone in codeplug.zones}
    resolved: dict[str, int] = {}
    for zone_id, value in mapping.items():
        if zone_id not in zone_ids:
            raise ValueError(
                f"region_indices names unknown zone {zone_id!r}"
            )
        index = int(value)
        if index < 0 or (max_zones is not None and index >= int(max_zones)):
            raise ValueError(
                f"region_indices puts zone {zone_id!r} at region {index}, "
                f"outside the radio's {max_zones} regions"
            )
        resolved[zone_id] = index

    used: dict[int, str] = {}
    for position, zone in enumerate(codeplug.zones):
        index = resolved.get(zone.id, position)
        if index in used:
            raise ValueError(
                f"zones {used[index]!r} and {zone.id!r} both map to region "
                f"{index}; one would overwrite the other"
            )
        used[index] = zone.id
    return resolved


def benlink_plan_from_resolved(
    codeplug: ResolvedCodeplug,
    *,
    capabilities: dict[str, Any] | None = None,
    blank_unlisted: bool = True,
    generated_by: str | None = None,
) -> dict[str, Any]:
    """Return a ``benlink-codeplug`` plan dict for ``codeplug``.

    ``blank_unlisted`` defaults to true: a generated plan is the whole truth
    for the regions it names, so leftovers from a previous codeplug are
    cleared rather than left to confuse whoever is spinning the dial. Pass
    false to emit a patch that only touches the listed slots.

    ``capabilities`` supplies the radio identity and geometry. Without it the
    plan still applies, but ``apply_codeplug.py`` loses its check that the
    connected radio is the model the plan was built for.
    """
    capabilities = capabilities or {}
    benlink_caps = capabilities.get("benlink", {})
    limits = capabilities.get("limits", {})

    max_zones = limits.get("max_zones")
    max_per_zone = limits.get("max_channels_per_zone")

    if max_zones is not None and len(codeplug.zones) > int(max_zones):
        raise ValueError(
            f"profile has {len(codeplug.zones)} zones; "
            f"{capabilities.get('name', codeplug.radio_id)} has "
            f"{max_zones} regions. Regions are the radio's only channel "
            "banks, so zones cannot be merged automatically."
        )

    region_indices = _region_indices(codeplug, max_zones)

    by_reference = {channel.reference: channel for channel in codeplug.channels}
    placed: set[str] = set()
    regions: list[dict[str, Any]] = []

    for position, zone in enumerate(codeplug.zones):
        index = region_indices.get(zone.id, position)
        if max_per_zone is not None and len(zone.channel_references) > int(max_per_zone):
            raise ValueError(
                f"zone '{zone.name}' has {len(zone.channel_references)} "
                f"channels; each region holds {max_per_zone}"
            )

        channels = []
        for slot, reference in enumerate(zone.channel_references, start=1):
            channel = by_reference.get(reference)
            if channel is None:
                raise ValueError(
                    f"zone '{zone.name}' references unknown channel {reference!r}"
                )
            channels.append(_channel_entry(slot, channel))
            placed.add(reference)

        regions.append({
            "index": index,
            "name": zone.name[:MAX_NAME_CHARS],
            "blank_unlisted": blank_unlisted,
            "channels": channels,
        })

    # No global channel pool exists on this radio, so an unplaced channel is
    # not "unassigned", it is unrepresentable. Surface it instead of writing
    # a plan that quietly omits it.
    orphans = [ref for ref in by_reference if ref not in placed]
    if orphans:
        raise ValueError(
            "these channels are in no zone and the radio has no global "
            "channel pool to hold them: " + ", ".join(sorted(orphans))
        )

    radio: dict[str, Any] = {"model": codeplug.radio_id}
    for key in ("vendor_id", "product_id"):
        if key in benlink_caps:
            radio[key] = benlink_caps[key]
    if max_zones is not None:
        radio["region_count"] = int(max_zones)
    if max_per_zone is not None:
        radio["channels_per_region"] = int(max_per_zone)

    regions.sort(key=lambda region: region["index"])

    plan: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "version": PLAN_VERSION,
        "name": codeplug.radio_instance_id or codeplug.radio_id,
        "radio": radio,
        "regions": regions,
    }
    if generated_by:
        plan["generated_by"] = generated_by
    return plan


def benlink_plan_json_from_resolved(
    codeplug: ResolvedCodeplug,
    *,
    capabilities: dict[str, Any] | None = None,
    blank_unlisted: bool = True,
    generated_by: str | None = None,
) -> str:
    """Return the plan as pretty-printed JSON text."""

    plan = benlink_plan_from_resolved(
        codeplug,
        capabilities=capabilities,
        blank_unlisted=blank_unlisted,
        generated_by=generated_by,
    )
    return json.dumps(plan, indent=2) + "\n"


def write_benlink_plan(
    path: Path,
    codeplug: ResolvedCodeplug,
    *,
    capabilities: dict[str, Any] | None = None,
    blank_unlisted: bool = True,
    generated_by: str | None = None,
) -> None:
    """Write the plan JSON to ``path``."""

    path.write_text(
        benlink_plan_json_from_resolved(
            codeplug,
            capabilities=capabilities,
            blank_unlisted=blank_unlisted,
            generated_by=generated_by,
        ),
        encoding="utf-8",
    )
