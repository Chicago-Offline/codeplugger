"""Fit an abstract use case onto one radio's capabilities.

A use-case base profile deliberately carries more than any single radio holds:
the GMRS base is 15 transmit channels, 7 receive-only interstitials and the
NOAA weather channels, which is three zones and 29 channels on a radio that
may have one zone and sixteen slots. Committed profiles solve that by hand
with `zones_only`. This module does the same thing on demand, so a new
operator does not have to know their radio's channel budget to get a codeplug.

Two kinds of removal happen here, and they are reported separately because
they mean different things:

- An **assignment** is dropped when the radio physically cannot use it: a
  VHF marine channel on a UHF-only handheld. Nothing the operator does with
  zone selection brings it back.
- A **zone** is dropped when it does not fit: the radio is full, or it holds
  fewer zones than the use case has. That is a budget decision, and the
  operator can override it with an explicit zone selection.

Priority is the order the base lists its zones, which is why the bases are
written most-important-first. Fitting never reorders, never merges and never
silently substitutes; the caller is expected to show every drop to a human
before anything reaches a radio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .catalog import Radio, UseCase
from .profile import (
    ProfileValidationError,
    _assignment_channel_counts,
    _assignment_id,
    _check_radio_support,
    _resolve_firmware_limits,
    _spacer_target,
)
from .validation import ValidationReport


@dataclass(frozen=True)
class Drop:
    """One thing the radio could not take, and why."""

    kind: str
    id: str
    name: str
    reason: str
    zone_id: str | None = None

    def format(self) -> str:
        where = f" from zone '{self.zone_id}'" if self.zone_id else ""
        return f"{self.kind} '{self.name}'{where}: {self.reason}"


@dataclass(frozen=True)
class FittedProfile:
    """A concrete, radio-bound profile plus the record of what it cost."""

    profile: dict[str, Any]
    drops: tuple[Drop, ...]
    channel_count: int

    @property
    def zone_count(self) -> int:
        return len(self.profile["zones"])

    @property
    def dropped_zones(self) -> tuple[Drop, ...]:
        return tuple(drop for drop in self.drops if drop.kind == "zone")

    @property
    def dropped_assignments(self) -> tuple[Drop, ...]:
        return tuple(drop for drop in self.drops if drop.kind == "assignment")


def _unsupported_assignments(
    capabilities: Mapping[str, Any],
    documents: Sequence[Any],
    assignment_ids: Sequence[str],
) -> dict[str, str]:
    """Return the reason each assignment the radio cannot use is unusable.

    Checked with the same function profile validation uses, so fitting can
    never keep something validation would then reject. The whole set is tried
    first because the common case is that everything fits and one pass over
    the SSRF documents answers it.
    """

    bulk = ValidationReport()
    _check_radio_support(bulk, capabilities, documents, set(assignment_ids))
    if not bulk.has_critical:
        return {}

    reasons: dict[str, str] = {}
    for assignment_id in assignment_ids:
        report = ValidationReport()
        _check_radio_support(report, capabilities, documents, {assignment_id})
        for issue in report.issues:
            # One reason per assignment: a VHF channel on a UHF radio fails the
            # RX and the TX check and says the same thing twice.
            reasons[assignment_id] = issue.format()
            break
    return reasons


def fit_use_case(
    use_case: UseCase,
    radio: Radio,
    documents: Sequence[Any],
    *,
    zones: Sequence[str] | None = None,
) -> FittedProfile:
    """Build the largest profile of ``use_case`` that ``radio`` can hold.

    ``zones`` restricts the candidates before fitting, the way a hand-written
    profile's `zones_only` does. Fitting still applies to what remains: an
    explicit selection that does not fit is trimmed and reported, not forced
    onto a radio that would truncate it silently.
    """

    candidates = list(use_case.profile["zones"])
    if zones is not None:
        wanted = list(dict.fromkeys(zones))
        known = {str(zone["id"]) for zone in candidates}
        unknown = [zone_id for zone_id in wanted if zone_id not in known]
        if unknown:
            raise ProfileValidationError(
                f"use case '{use_case.id}' has no zone(s) "
                f"{', '.join(unknown)}; it offers: {', '.join(sorted(known))}"
            )
        candidates = [zone for zone in candidates if str(zone["id"]) in wanted]

    counts, ambiguous = _assignment_channel_counts(documents)
    limits, limit_source = _resolve_firmware_limits(radio.capabilities, None)
    all_ids = [
        _assignment_id(assignment)
        for zone in candidates
        for assignment in zone["assignments"]
        if _spacer_target(assignment) is None
    ]
    unusable = _unsupported_assignments(radio.capabilities, documents, all_ids)

    drops: list[Drop] = []
    kept_zones: list[dict[str, Any]] = []
    kept_ids: set[str] = set()
    total_channels = 0

    for zone in candidates:
        zone_id = str(zone["id"])
        zone_name = str(zone["name"])
        assignments: list[Any] = []
        zone_drops: list[Drop] = []
        zone_channels = 0
        new_channels = 0
        for assignment in zone["assignments"]:
            if _spacer_target(assignment) is not None:
                assignments.append(assignment)
                continue
            assignment_id = _assignment_id(assignment)
            if assignment_id in ambiguous or assignment_id not in counts:
                zone_drops.append(
                    Drop(
                        kind="assignment",
                        id=assignment_id,
                        name=assignment_id,
                        zone_id=zone_id,
                        reason=(
                            "resolves to more than one channel set in the SSRF "
                            "data"
                            if assignment_id in ambiguous
                            else "is not in the SSRF data"
                        ),
                    )
                )
                continue
            if assignment_id in unusable:
                zone_drops.append(
                    Drop(
                        kind="assignment",
                        id=assignment_id,
                        name=assignment_id,
                        zone_id=zone_id,
                        reason=unusable[assignment_id],
                    )
                )
                continue
            assignments.append(assignment)
            zone_channels += counts[assignment_id]
            if assignment_id not in kept_ids:
                new_channels += counts[assignment_id]

        if not zone_channels:
            # The whole zone went, so say that once instead of listing every
            # weather channel a UHF-only radio cannot hear.
            drops.append(
                Drop(
                    kind="zone",
                    id=zone_id,
                    name=zone_name,
                    reason=(
                        f"none of its {len(zone_drops)} assignment(s) work on "
                        f"{radio.name}: {zone_drops[0].reason}"
                        if zone_drops
                        else f"holds nothing {radio.name} can use"
                    ),
                )
            )
            continue
        drops.extend(zone_drops)
        if zone_channels > limits["max_channels_per_zone"]:
            drops.append(
                Drop(
                    kind="zone",
                    id=zone_id,
                    name=zone_name,
                    reason=(
                        f"expands to {zone_channels} channels; "
                        f"{limit_source} is {limits['max_channels_per_zone']} "
                        "per zone"
                    ),
                )
            )
            continue
        if len(kept_zones) >= limits["max_zones"]:
            drops.append(
                Drop(
                    kind="zone",
                    id=zone_id,
                    name=zone_name,
                    reason=(
                        f"{radio.name} holds {limits['max_zones']} zone(s), "
                        "already filled by higher-priority zones"
                    ),
                )
            )
            continue
        if total_channels + new_channels > limits["max_channels"]:
            drops.append(
                Drop(
                    kind="zone",
                    id=zone_id,
                    name=zone_name,
                    reason=(
                        f"needs {new_channels} more channels; "
                        f"{limit_source} is {limits['max_channels']} and "
                        f"{total_channels} are already used"
                    ),
                )
            )
            continue

        kept_zones.append({**zone, "assignments": assignments})
        kept_ids.update(
            _assignment_id(a) for a in assignments if _spacer_target(a) is None
        )
        total_channels += new_channels

    if not kept_zones:
        raise ProfileValidationError(
            f"nothing from use case '{use_case.id}' fits {radio.name}:\n"
            + "\n".join(f"  {drop.format()}" for drop in drops)
        )

    profile = {
        key: value
        for key, value in use_case.profile.items()
        if key not in {"$schema", "id", "name", "zones"}
    }
    profile.update(
        {
            "version": "0.1",
            "id": f"{radio.id}_{use_case.id}",
            "name": f"{radio.name} {use_case.name}",
            "radio": radio.id,
            "zones": kept_zones,
        }
    )
    return FittedProfile(
        profile=profile,
        drops=tuple(drops),
        channel_count=total_channels,
    )
