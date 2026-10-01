"""Resolve validated profiles into exporter-neutral codeplug data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from ssrf.emissions import mode_from_emission

from .emission import bandwidth_khz_from_emission
from .profile import (
    DEFAULT_RADIO_ROOT,
    DEFAULT_SCHEMA_PATH,
    ProfileValidationError,
    _check_name_length,
    _contact_kind,
    _load_and_validate_profile,
    _load_capabilities,
    _spacer_target,
    _ssrf_contacts,
)
from .validation import ValidationReport


@dataclass(frozen=True)
class ResolvedTones:
    """Format-neutral analog tone settings."""

    ctcss_tx_hz: float | None = None
    ctcss_rx_hz: float | None = None
    dcs_tx_code: str | int | None = None
    dcs_rx_code: str | int | None = None


@dataclass(frozen=True)
class ResolvedContact:
    """A DMR contact selected from SSRF facts by profile policy."""

    id: str
    name: str
    number: int
    kind: str  # "group", "private", or "all"
    default_timeslot: int | None = None


@dataclass(frozen=True)
class ResolvedRxGroup:
    """A DMR RX group list with ordered references to resolved contacts."""

    id: str
    name: str
    contact_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedScanList:
    """A scan list with ordered references to resolved channels."""

    id: str
    name: str
    channel_references: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedChannel:
    """One channel after profile selection and SSRF overlay resolution."""

    reference: str
    assignment_id: str
    display_name: str
    rx_frequency_mhz: float
    tx_frequency_mhz: float | None
    mode: str | None
    service: str | None
    tones: ResolvedTones
    tx_permitted: bool
    notes: str | None = None
    bandwidth_khz: float | None = None
    power_w: float | None = None
    color_code: int | None = None
    timeslots: tuple[int, ...] = ()
    timeslot: int | None = None
    contact_id: str | None = None
    rx_group_id: str | None = None
    scan_list_id: str | None = None
    dmr_id_key: str | None = None
    dmr_id: int | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    channel_number: int | None = None


@dataclass(frozen=True)
class ResolvedZone:
    """A profile zone with ordered references to resolved channels."""

    id: str
    name: str
    channel_references: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedCodeplug:
    """Exporter-neutral representation of a selected radio profile."""

    radio_id: str
    radio_instance_id: str
    radio_instance: dict[str, Any] | None
    channels: tuple[ResolvedChannel, ...]
    zones: tuple[ResolvedZone, ...]
    contacts: tuple[ResolvedContact, ...] = ()
    rx_groups: tuple[ResolvedRxGroup, ...] = ()
    scan_lists: tuple[ResolvedScanList, ...] = ()
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a structure containing only JSON/YAML data types."""

        data = asdict(self)
        data["channels"] = list(data["channels"])
        data["zones"] = [
            {**zone, "channel_references": list(zone["channel_references"])}
            for zone in data["zones"]
        ]
        data["contacts"] = list(data["contacts"])
        data["rx_groups"] = [
            {**group, "contact_ids": list(group["contact_ids"])}
            for group in data["rx_groups"]
        ]
        data["scan_lists"] = [
            {**scan, "channel_references": list(scan["channel_references"])}
            for scan in data["scan_lists"]
        ]
        return data

    def to_json(self) -> str:
        """Serialize deterministically as human-readable JSON."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_yaml(self) -> str:
        """Serialize deterministically as human-readable YAML."""

        return yaml.safe_dump(self.to_dict(), sort_keys=True)

    def numbered_channels(self) -> tuple[tuple[int, ResolvedChannel], ...]:
        """Pair each channel with the channel number it occupies on the radio.

        Profiles may leave gaps via ``next_channel`` spacers, so the number is
        not the channel's position in this list. Channels built outside
        ``build_codeplug`` carry no number and fall back to their position.
        """

        return tuple(
            (
                channel.channel_number
                if channel.channel_number is not None
                else position,
                channel,
            )
            for position, channel in enumerate(self.channels, start=1)
        )


def _display_name(
    assignment: Any,
    fallback: str,
    override: str | None,
    *,
    short_name: str | None = None,
    max_chars: int | None = None,
    use_assignment_short_name: bool = True,
) -> str:
    name = override or assignment.display_name or assignment.channel_name or fallback
    authored_short_name = (
        getattr(assignment, "short_name", None)
        if use_assignment_short_name
        else None
    ) or short_name
    if max_chars is not None and len(name) > max_chars and authored_short_name:
        return authored_short_name
    return name


def _bandwidth_khz(source: Any | None) -> float | None:
    """Return an explicit bandwidth, else derive one from the emission.

    SSRF-Lite marks ``bandwidth_khz`` optional and much of the public data
    carries only the ITU emission designator, so an explicit value always wins
    and the designator is the fallback rather than the source of truth.
    """

    if source is None:
        return None
    explicit = getattr(source, "bandwidth_khz", None)
    if explicit is not None:
        return explicit
    return bandwidth_khz_from_emission(getattr(source, "emission", None))


def _select_timeslot(
    timeslots: Sequence[int],
    override: int | None,
    contact_default: int | None,
    assignment_id: str,
) -> int | None:
    """Pick the timeslot a DMR channel sits on.

    A receiver can only listen to one slot at a time, so a two-slot repeater
    needs one channel per slot. Precedence, most explicit first:

    1. ``timeslot`` on the profile assignment - the operator said so.
    2. ``default_timeslot`` on the SSRF contact - the talkgroup is observed on
       that slot, so a channel carrying it belongs there.
    3. The first slot the chain declares, which is the historical behaviour.

    A slot the chain does not declare is always an error: silently moving a
    channel to a slot the repeater does not use produces a channel that hears
    nothing, which is far harder to debug than a failed build.
    """

    available = tuple(timeslots or ())
    if not available:
        return None
    for value, source in ((override, "profile assignment"), (contact_default, "contact default_timeslot")):
        if value is None:
            continue
        if value not in available:
            raise ProfileValidationError(
                f"assignment '{assignment_id}': {source} requests timeslot "
                f"{value}, but the chain only declares "
                f"{', '.join(str(item) for item in available)}."
            )
        return value
    return available[0]


def _tones(mode: Any | None, *, station_perspective: bool = False) -> ResolvedTones:
    if mode is None:
        return ResolvedTones()
    tx_side = "rx" if station_perspective else "tx"
    rx_side = "tx" if station_perspective else "rx"
    return ResolvedTones(
        ctcss_tx_hz=getattr(mode, f"ctcss_{tx_side}_hz"),
        ctcss_rx_hz=getattr(mode, f"ctcss_{rx_side}_hz"),
        dcs_tx_code=getattr(mode, f"dcs_{tx_side}_code"),
        dcs_rx_code=getattr(mode, f"dcs_{rx_side}_code"),
    )


def _resolve_assignment(
    document: Any,
    assignment: Any,
    display_name_override: str | None = None,
    extensions: dict[str, Any] | None = None,
    contact_id: str | None = None,
    rx_group_id: str | None = None,
    scan_list_id: str | None = None,
    dmr_id_key: str | None = None,
    dmr_id: int | None = None,
    start_number: int = 1,
    timeslot_override: int | None = None,
    contact_default_timeslot: int | None = None,
    max_channel_name_chars: int | None = None,
) -> list[ResolvedChannel]:
    reference = document.reference
    extensions = extensions or {}
    authorization = next(
        (
            item
            for item in reference.authorizations
            if item.id == assignment.authorization_id
        ),
        None,
    )

    rf_chain = next(
        (item for item in reference.rf_chains if item.id == assignment.rf_chain_id),
        None,
    )
    if rf_chain is not None:
        station = next(
            (item for item in reference.stations if item.id == rf_chain.station_id),
            None,
        )
        station_tx_frequency = rf_chain.tx.freq_mhz
        station_rx_frequency = rf_chain.rx.freq_mhz
        rx_frequency = station_tx_frequency or station_rx_frequency
        tx_frequency = station_rx_frequency or station_tx_frequency
        tx_permitted = (
            station_tx_frequency is not None
            and (
                station_rx_frequency is not None
                or assignment.usage in {"call", "simplex"}
            )
        )
        return [
            ResolvedChannel(
                reference=assignment.id,
                assignment_id=assignment.id,
                display_name=_display_name(
                    assignment,
                    assignment.id,
                    display_name_override,
                    max_chars=max_channel_name_chars,
                ),
                rx_frequency_mhz=rx_frequency,
                tx_frequency_mhz=tx_frequency if tx_permitted else None,
                mode=rf_chain.mode.type,
                service=(
                    assignment.service
                    or (authorization.service if authorization else None)
                    or (station.service if station else None)
                ),
                tones=_tones(rf_chain.mode, station_perspective=True),
                tx_permitted=tx_permitted,
                notes=assignment.notes,
                channel_number=start_number,
                bandwidth_khz=_bandwidth_khz(rf_chain.tx),
                power_w=rf_chain.tx.power_w,
                color_code=rf_chain.mode.color_code,
                timeslots=tuple(rf_chain.mode.timeslots or ()),
                timeslot=_select_timeslot(
                    rf_chain.mode.timeslots or (),
                    timeslot_override,
                    contact_default_timeslot,
                    assignment.id,
                ),
                contact_id=contact_id,
                rx_group_id=rx_group_id,
                scan_list_id=scan_list_id,
                dmr_id_key=dmr_id_key,
                dmr_id=dmr_id,
                extensions=extensions,
            )
        ]

    plan = next(
        (
            item
            for item in reference.channel_plans
            if item.id == assignment.channel_plan_id
        ),
        None,
    )
    if plan is None:
        return []
    selected_channels = [
        channel
        for channel in plan.channels
        if assignment.channel_name is None or channel.name == assignment.channel_name
    ]
    expanded: list[tuple[Any, Any | None, str | None, bool]] = []
    for channel in selected_channels:
        emissions = channel.permitted_emissions()
        if len(emissions) > 1:
            expanded.extend(
                (
                    channel,
                    emission,
                    emission.mode or mode_from_emission(emission.emission),
                    True,
                )
                for emission in emissions
            )
        else:
            emission = emissions[0] if emissions else None
            expanded.append(
                (
                    channel,
                    emission,
                    (
                        emission.mode
                        if emission and emission.mode
                        else mode_from_emission(emission.emission)
                        if emission
                        else None
                    ),
                    False,
                )
            )

    expands_plan = len(expanded) > 1
    resolved_channels = []
    for offset, (channel, emission, emission_mode, emission_variant) in enumerate(
        expanded
    ):
        name = _display_name(
            assignment,
            channel.name,
            display_name_override if len(selected_channels) == 1 else None,
            short_name=channel.short_name,
            max_chars=max_channel_name_chars,
            use_assignment_short_name=len(selected_channels) == 1,
        )
        mode = channel.mode
        mode_type = (
            emission_mode
            or (mode.type if mode is not None else None)
            or mode_from_emission(emission.emission if emission else channel.emission)
        )
        suffix = emission_mode or (emission.emission if emission else None)
        if emission_variant and suffix:
            name = f"{name} {suffix}"

        station_rx_frequency = channel.rx_freq_mhz
        tx_permitted = (
            station_rx_frequency is not None
            or assignment.usage in {"call", "simplex"}
        )
        emission_bandwidth = getattr(emission, "bandwidth_khz", None)
        if emission_bandwidth is None and emission is not None:
            emission_bandwidth = bandwidth_khz_from_emission(emission.emission)
        resolved_channels.append(
            ResolvedChannel(
                reference=(
                    f"{assignment.id}:{channel.name}:{suffix}"
                    if emission_variant and suffix
                    else f"{assignment.id}:{channel.name}:{offset}"
                    if emission_variant
                    else f"{assignment.id}:{channel.name}"
                    if expands_plan
                    else assignment.id
                ),
                assignment_id=assignment.id,
                display_name=name,
                rx_frequency_mhz=channel.freq_mhz,
                tx_frequency_mhz=(
                    (station_rx_frequency or channel.freq_mhz)
                    if tx_permitted
                    else None
                ),
                mode=mode_type,
                service=(
                    assignment.service
                    or (authorization.service if authorization else None)
                    or plan.service
                ),
                tones=_tones(mode, station_perspective=True),
                tx_permitted=tx_permitted,
                notes=assignment.notes or channel.notes,
                channel_number=start_number + offset,
                bandwidth_khz=(
                    emission_bandwidth or _bandwidth_khz(channel)
                ),
                power_w=getattr(emission, "power_w", None),
                color_code=getattr(mode, "color_code", None),
                timeslots=tuple(getattr(mode, "timeslots", None) or ()),
                timeslot=_select_timeslot(
                    getattr(mode, "timeslots", None) or (),
                    timeslot_override,
                    contact_default_timeslot,
                    assignment.id,
                ),
                contact_id=contact_id,
                rx_group_id=rx_group_id,
                scan_list_id=scan_list_id,
                dmr_id_key=dmr_id_key,
                dmr_id=dmr_id,
                extensions=extensions,
            )
        )
    return resolved_channels


def resolve_codeplug(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    instance_registry_path: Path | None = None,
    report: ValidationReport | None = None,
) -> ResolvedCodeplug:
    """Validate and normalize a profile plus precedence-ordered SSRF roots.

    Pass `report` to receive load-time and resolve-time issues in one place;
    otherwise non-critical findings are discarded.
    """

    profile, documents, instance_metadata, load_report = _load_and_validate_profile(
        profile_path,
        ssrf_roots,
        schema_path=schema_path,
        radio_root=radio_root,
        instance_registry_path=instance_registry_path,
    )
    if report is None:
        report = ValidationReport()
    report.extend(load_report)
    return build_codeplug(
        profile,
        documents,
        instance_metadata,
        radio_root=radio_root,
        report=report,
    )


def build_codeplug(
    profile: Mapping[str, Any],
    documents: Sequence[Any],
    instance_metadata: Mapping[str, Any] | None,
    *,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    report: ValidationReport | None = None,
) -> ResolvedCodeplug:
    """Build a codeplug from an already-loaded profile.

    Split out of `resolve_codeplug` so callers that have already loaded a
    profile can resolve it without parsing twice. Channel display names do not
    exist until assignments resolve, so the channel-name length check lives
    here and is only reached by resolving.
    """

    if report is None:
        report = ValidationReport()
    limits = _load_capabilities(profile["radio"], radio_root)["limits"]
    assignments = {
        assignment.id: (document, assignment)
        for document in documents
        for assignment in document.reference.assignments
    }

    channels: list[ResolvedChannel] = []
    zones: list[ResolvedZone] = []
    assignment_channel_refs: dict[str, tuple[str, ...]] = {}
    contact_order: list[str] = []
    # Indexed before the zone loop because channel resolution reads each
    # contact's default_timeslot to place the channel on the right slot.
    ssrf_contacts = _ssrf_contacts(documents)
    dmr_id_map = {
        entry["key"]: entry
        for entry in (instance_metadata or {}).get("dmr_ids", []) or []
    }
    default_dmr_id_key = (instance_metadata or {}).get("default_dmr_id")
    legacy_dmr_id = (instance_metadata or {}).get("dmr_id")
    next_channel_number = 1
    for zone in profile["zones"]:
        channel_references: list[str] = []
        zone_dmr_id_key = zone.get("dmr_id")
        for assignment_value in zone["assignments"]:
            spacer_target = _spacer_target(assignment_value)
            if spacer_target is not None:
                next_channel_number = spacer_target
                continue
            assignment_id = (
                assignment_value["id"]
                if isinstance(assignment_value, dict)
                else assignment_value
            )
            document, assignment = assignments[assignment_id]
            display_name_override = (
                assignment_value.get("display_name")
                if isinstance(assignment_value, dict)
                else None
            )
            assignment_extensions = (
                assignment_value.get("extensions", {})
                if isinstance(assignment_value, dict)
                else {}
            )
            contact_id = rx_group_id = scan_list_id = None
            assignment_dmr_id_key = None
            timeslot_override = None
            if isinstance(assignment_value, dict):
                contact_id = assignment_value.get("contact")
                rx_group_id = assignment_value.get("rx_group")
                scan_list_id = assignment_value.get("scan_list")
                assignment_dmr_id_key = assignment_value.get("dmr_id")
                timeslot_override = assignment_value.get("timeslot")
            contact_default_timeslot = None
            if contact_id is not None:
                selected_contact = ssrf_contacts.get(contact_id)
                if selected_contact is not None:
                    contact_default_timeslot = getattr(
                        selected_contact, "default_timeslot", None
                    )
            if contact_id is not None and contact_id not in contact_order:
                contact_order.append(contact_id)
            effective_dmr_id_key = (
                assignment_dmr_id_key or zone_dmr_id_key or default_dmr_id_key
            )
            if effective_dmr_id_key is not None and effective_dmr_id_key in dmr_id_map:
                dmr_id_key = effective_dmr_id_key
                dmr_id = int(dmr_id_map[effective_dmr_id_key]["id"])
            else:
                # Either no key was requested anywhere in the precedence chain,
                # or (defensively) it points at an unknown key that
                # _load_and_validate_profile already rejected as critical.
                dmr_id_key = None
                dmr_id = int(legacy_dmr_id) if legacy_dmr_id is not None else None
            resolved_channels = _resolve_assignment(
                document,
                assignment,
                display_name_override,
                assignment_extensions,
                contact_id,
                rx_group_id,
                scan_list_id,
                dmr_id_key,
                dmr_id,
                next_channel_number,
                timeslot_override,
                contact_default_timeslot,
                limits.get("max_channel_name_chars"),
            )
            next_channel_number += len(resolved_channels)
            for resolved_channel in resolved_channels:
                _check_name_length(
                    report,
                    limits,
                    "max_channel_name_chars",
                    "channel",
                    resolved_channel.display_name,
                )
            channels.extend(resolved_channels)
            assignment_channel_refs[assignment_id] = tuple(
                channel.reference for channel in resolved_channels
            )
            channel_references.extend(
                channel.reference for channel in resolved_channels
            )
        zones.append(
            ResolvedZone(
                id=zone["id"],
                name=zone["name"],
                channel_references=tuple(channel_references),
            )
        )

    if report.has_critical:
        raise ProfileValidationError(report.critical_message())

    rx_group_contact_ids = [
        contact_id
        for group in profile.get("rx_groups", [])
        for contact_id in group["contacts"]
    ]
    seen_contacts: set[str] = set()
    contacts: list[ResolvedContact] = []
    for contact_id in [*rx_group_contact_ids, *contact_order]:
        if contact_id in seen_contacts:
            continue
        seen_contacts.add(contact_id)
        contact = ssrf_contacts[contact_id]
        kind = _contact_kind(contact.kind)
        assert kind is not None and contact.number is not None  # validated above
        contacts.append(
            ResolvedContact(
                id=contact.id,
                name=contact.name,
                number=int(contact.number),
                kind=kind,
                default_timeslot=getattr(contact, "default_timeslot", None),
            )
        )
    rx_groups = tuple(
        ResolvedRxGroup(
            id=group["id"],
            name=group["name"],
            contact_ids=tuple(group["contacts"]),
        )
        for group in profile.get("rx_groups", [])
    )
    scan_lists = tuple(
        ResolvedScanList(
            id=scan_list["id"],
            name=scan_list["name"],
            channel_references=tuple(
                reference
                for assignment_id in scan_list["channels"]
                for reference in assignment_channel_refs[assignment_id]
            ),
        )
        for scan_list in profile.get("scan_lists", [])
    )

    return ResolvedCodeplug(
        radio_id=profile["radio"],
        radio_instance_id=profile.get("radio_instance", profile["id"]),
        radio_instance=instance_metadata,
        channels=tuple(channels),
        zones=tuple(zones),
        contacts=tuple(contacts),
        rx_groups=rx_groups,
        scan_lists=scan_lists,
        extensions=profile.get("extensions", {}),
    )