"""Resolve validated profiles into exporter-neutral codeplug data."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Sequence

import yaml

from .profile import (
    DEFAULT_RADIO_ROOT,
    DEFAULT_SCHEMA_PATH,
    _check_name_length,
    _load_and_validate_profile,
    _load_capabilities,
)


@dataclass(frozen=True)
class ResolvedTones:
    """Format-neutral analog tone settings."""

    ctcss_tx_hz: float | None = None
    ctcss_rx_hz: float | None = None
    dcs_tx_code: str | int | None = None
    dcs_rx_code: str | int | None = None


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

    def to_dict(self) -> dict[str, Any]:
        """Return a structure containing only JSON/YAML data types."""

        data = asdict(self)
        data["channels"] = list(data["channels"])
        data["zones"] = [
            {**zone, "channel_references": list(zone["channel_references"])}
            for zone in data["zones"]
        ]
        return data

    def to_json(self) -> str:
        """Serialize deterministically as human-readable JSON."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_yaml(self) -> str:
        """Serialize deterministically as human-readable YAML."""

        return yaml.safe_dump(self.to_dict(), sort_keys=True)


def _display_name(assignment: Any, fallback: str, override: str | None) -> str:
    return override or assignment.display_name or assignment.channel_name or fallback


def _tones(mode: Any | None) -> ResolvedTones:
    if mode is None:
        return ResolvedTones()
    return ResolvedTones(
        ctcss_tx_hz=mode.ctcss_tx_hz,
        ctcss_rx_hz=mode.ctcss_rx_hz,
        dcs_tx_code=mode.dcs_tx_code,
        dcs_rx_code=mode.dcs_rx_code,
    )


def _resolve_assignment(
    document: Any, assignment: Any, display_name_override: str | None = None
) -> list[ResolvedChannel]:
    reference = document.reference
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
        rx_frequency = rf_chain.rx.freq_mhz
        tx_frequency = rf_chain.tx.freq_mhz
        return [
            ResolvedChannel(
                reference=assignment.id,
                assignment_id=assignment.id,
                display_name=_display_name(
                    assignment, assignment.id, display_name_override
                ),
                rx_frequency_mhz=rx_frequency,
                tx_frequency_mhz=tx_frequency,
                mode=rf_chain.mode.type,
                service=(
                    assignment.service
                    or (authorization.service if authorization else None)
                    or (station.service if station else None)
                ),
                tones=_tones(rf_chain.mode),
                tx_permitted=tx_frequency is not None,
                notes=assignment.notes,
                bandwidth_khz=rf_chain.tx.bandwidth_khz,
                power_w=rf_chain.tx.power_w,
                color_code=rf_chain.mode.color_code,
                timeslots=tuple(rf_chain.mode.timeslots or ()),
                timeslot=(
                    rf_chain.mode.timeslots[0]
                    if rf_chain.mode.timeslots
                    else None
                ),
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
    expands_plan = len(selected_channels) > 1
    return [
        ResolvedChannel(
            reference=(
                f"{assignment.id}:{channel.name}"
                if expands_plan
                else assignment.id
            ),
            assignment_id=assignment.id,
            display_name=_display_name(
                assignment, channel.name, display_name_override
            ),
            rx_frequency_mhz=channel.freq_mhz,
            tx_frequency_mhz=(
                channel.tx_freq_mhz
                if channel.tx_freq_mhz is not None
                else (
                    channel.freq_mhz
                    if assignment.usage in {"call", "simplex"}
                    else None
                )
            ),
            mode=None,
            service=(
                assignment.service
                or (authorization.service if authorization else None)
                or plan.service
            ),
            tones=ResolvedTones(),
            tx_permitted=(
                channel.tx_freq_mhz is not None
                or assignment.usage in {"call", "simplex"}
            ),
            notes=assignment.notes or channel.notes,
            bandwidth_khz=channel.bandwidth_khz,
        )
        for channel in selected_channels
    ]


def resolve_codeplug(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    instance_registry_path: Path | None = None,
) -> ResolvedCodeplug:
    """Validate and normalize a profile plus precedence-ordered SSRF roots."""

    profile, documents, instance_metadata = _load_and_validate_profile(
        profile_path,
        ssrf_roots,
        schema_path=schema_path,
        radio_root=radio_root,
        instance_registry_path=instance_registry_path,
    )
    limits = _load_capabilities(profile["radio"], radio_root)["limits"]
    assignments = {
        assignment.id: (document, assignment)
        for document in documents
        for assignment in document.reference.assignments
    }

    channels: list[ResolvedChannel] = []
    zones: list[ResolvedZone] = []
    for zone in profile["zones"]:
        channel_references: list[str] = []
        for assignment_value in zone["assignments"]:
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
            resolved_channels = _resolve_assignment(
                document, assignment, display_name_override
            )
            for resolved_channel in resolved_channels:
                _check_name_length(
                    limits,
                    "max_channel_name_chars",
                    "channel",
                    resolved_channel.display_name,
                )
            channels.extend(resolved_channels)
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

    return ResolvedCodeplug(
        radio_id=profile["radio"],
        radio_instance_id=profile.get("radio_instance", profile["id"]),
        radio_instance=instance_metadata,
        channels=tuple(channels),
        zones=tuple(zones),
    )