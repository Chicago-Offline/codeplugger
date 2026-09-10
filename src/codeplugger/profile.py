"""Validate Codeplugger profiles against schema, radio, and SSRF inputs."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
import yaml

from .emission import bandwidth_khz_from_emission
from .validation import ValidationReport


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "profile-0.1.schema.json"
DEFAULT_CAPABILITIES_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "capabilities-0.2.schema.json"
)
DEFAULT_INSTANCE_REGISTRY_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "instance-registry-0.1.schema.json"
)
DEFAULT_RADIO_ROOT = PROJECT_ROOT / "radios"


class ProfileValidationError(ValueError):
    """Raised when a profile cannot be resolved into a valid selection."""


_MAX_PROFILE_INHERITANCE_DEPTH = 32
_PROFILE_INHERITANCE_KEYS = {"extends", "zones_only", "omit_zones"}


def _merge_profile_values(parent: Any, child: Any) -> Any:
    """Merge profile mappings and ID-keyed object lists recursively."""

    if isinstance(parent, Mapping) and isinstance(child, Mapping):
        merged = copy.deepcopy(dict(parent))
        for key, value in child.items():
            merged[key] = (
                _merge_profile_values(merged[key], value)
                if key in merged
                else copy.deepcopy(value)
            )
        return merged

    if isinstance(parent, list) and isinstance(child, list):
        items = parent + child
        if items and all(isinstance(item, Mapping) and "id" in item for item in items):
            merged = copy.deepcopy(parent)
            positions = {item["id"]: index for index, item in enumerate(merged)}
            for item in child:
                item_id = item["id"]
                if item_id in positions:
                    index = positions[item_id]
                    merged[index] = _merge_profile_values(merged[index], item)
                else:
                    positions[item_id] = len(merged)
                    merged.append(copy.deepcopy(item))
            return merged

    return copy.deepcopy(child)


def _profile_zone_filter(value: Any, key: str, profile_path: Path) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProfileValidationError(
            f"{profile_path}: '{key}' must be a list of zone IDs"
        )
    return value


def _load_profile_with_inheritance(
    profile_path: Path,
    *,
    stack: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Load a profile and resolve its relative inheritance chain."""

    profile_path = profile_path.resolve()
    if profile_path in stack:
        chain = " -> ".join(str(path) for path in (*stack, profile_path))
        raise ProfileValidationError(f"profile inheritance cycle: {chain}")
    if len(stack) >= _MAX_PROFILE_INHERITANCE_DEPTH:
        raise ProfileValidationError(
            f"{profile_path}: profile inheritance exceeds the maximum depth of "
            f"{_MAX_PROFILE_INHERITANCE_DEPTH}"
        )

    profile = _load_mapping(profile_path)
    extends = profile.get("extends")
    zones_only = _profile_zone_filter(profile.get("zones_only"), "zones_only", profile_path)
    omit_zones = _profile_zone_filter(profile.get("omit_zones"), "omit_zones", profile_path)
    if zones_only is not None and omit_zones is not None:
        raise ProfileValidationError(
            f"{profile_path}: 'zones_only' and 'omit_zones' are mutually exclusive"
        )

    if extends is None:
        merged = copy.deepcopy(profile)
    else:
        if not isinstance(extends, str) or not extends:
            raise ProfileValidationError(f"{profile_path}: 'extends' must be a path")
        parent_path = (profile_path.parent / extends).resolve()
        if "radio_instance" not in profile:
            raise ProfileValidationError(
                f"{profile_path}: child profiles must define 'radio_instance'"
            )
        try:
            parent = _load_profile_with_inheritance(
                parent_path, stack=(*stack, profile_path)
            )
        except OSError as exc:
            raise ProfileValidationError(
                f"{profile_path}: could not load parent profile '{extends}': {exc}"
            ) from exc
        if "radio" in profile and profile["radio"] != parent.get("radio"):
            raise ProfileValidationError(
                f"{profile_path}: profile radio '{profile.get('radio')}' does not "
                f"match parent radio '{parent.get('radio')}'"
            )
        parent.pop("id", None)
        parent.pop("radio_instance", None)
        child = {
            key: value
            for key, value in profile.items()
            if key not in _PROFILE_INHERITANCE_KEYS
        }
        merged = _merge_profile_values(parent, child)

    if zones_only is not None:
        zone_ids = set(zones_only)
        available = {
            zone["id"] for zone in merged.get("zones", []) if isinstance(zone, Mapping)
        }
        unknown = zone_ids - available
        if unknown:
            raise ProfileValidationError(
                f"{profile_path}: 'zones_only' references unknown zones: "
                f"{', '.join(sorted(unknown))}"
            )
        merged["zones"] = [
            zone for zone in merged.get("zones", []) if zone.get("id") in zone_ids
        ]
    elif omit_zones is not None:
        zone_ids = set(omit_zones)
        available = {
            zone["id"] for zone in merged.get("zones", []) if isinstance(zone, Mapping)
        }
        unknown = zone_ids - available
        if unknown:
            raise ProfileValidationError(
                f"{profile_path}: 'omit_zones' references unknown zones: "
                f"{', '.join(sorted(unknown))}"
            )
        merged["zones"] = [
            zone for zone in merged.get("zones", []) if zone.get("id") not in zone_ids
        ]

    for key in _PROFILE_INHERITANCE_KEYS:
        merged.pop(key, None)

    return merged


# SSRF contact ``kind`` spellings mapped to the neutral form exporters use.
_CONTACT_KINDS = {
    "group": "group",
    "private": "private",
    "all": "all",
    "allcall": "all",
}


def _contact_kind(kind: Any) -> str | None:
    """Normalize an SSRF contact kind; None when unrecognized."""

    return _CONTACT_KINDS.get(str(kind).strip().lower())


def _ssrf_contacts(documents: Sequence[Any]) -> dict[str, Any]:
    """Index SSRF contacts by id; later documents take precedence."""

    return {
        contact.id: contact
        for document in documents
        for contact in document.reference.contacts
    }


def _assignment_id(value: Any) -> str:
    return value["id"] if isinstance(value, dict) else value


def _load_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ProfileValidationError(f"{path}: expected a mapping")
    return data


def _format_schema_errors(errors: Sequence[Any]) -> str:
    lines: list[str] = []
    for error in errors:
        location = "/".join(str(part) for part in error.path) or "<root>"
        lines.append(f"{location}: {error.message}")
    return "; ".join(lines)


def _assignment_channel_counts(
    documents: Sequence[Any],
) -> tuple[dict[str, int], set[str]]:
    channel_counts: dict[str, int] = {}
    ambiguous: set[str] = set()
    for document in documents:
        reference = document.reference
        rf_chain_ids = {chain.id for chain in reference.rf_chains}
        channel_plans = {plan.id: plan for plan in reference.channel_plans}
        for assignment in reference.assignments:
            channel_count = 0
            if assignment.rf_chain_id in rf_chain_ids:
                channel_count = 1
            elif assignment.channel_plan_id in channel_plans:
                plan = channel_plans[assignment.channel_plan_id]
                matching_channels = [
                    channel
                    for channel in plan.channels
                    if channel.name == assignment.channel_name
                ]
                channel_count = len(matching_channels or plan.channels)
            if not channel_count:
                continue
            if assignment.id in channel_counts:
                ambiguous.add(assignment.id)
            else:
                channel_counts[assignment.id] = channel_count
    return channel_counts, ambiguous


def _resolve_firmware_limits(
    capabilities: Mapping[str, Any],
    firmware: str | None,
) -> tuple[dict[str, int], str]:
    """Return the effective limits for ``firmware`` plus a description of them.

    Some limits change across firmware versions on the same radio. The DM-32UV
    ROW line is the known case: ``DM32.01.L01.048`` stores 150,000 CSV contacts
    but has no record function, and ``DM32.01.01.049`` restores record and drops
    back to 50,000. A profile that only fits on 048 must not validate clean
    against a radio running 049.

    Resolution:

    * A firmware with an entry in ``firmware_limits`` uses ``limits`` updated by
      that entry.
    * A known radio with an *unknown* firmware uses the **most conservative**
      value across the base limits and every declared override, so an unverified
      radio cannot validate against a capacity no shipped firmware provides.
    * A radio declaring no ``firmware_limits`` behaves exactly as before.
    """

    base = dict(capabilities["limits"])
    overrides: Mapping[str, Mapping[str, int]] = capabilities.get(
        "firmware_limits", {}
    )
    if not overrides:
        return base, "radio limit"

    if firmware is not None and firmware in overrides:
        base.update(overrides[firmware])
        return base, f"limit for firmware {firmware}"

    # Unknown or undeclared firmware: take the floor of every possibility so
    # validation cannot pass something that fits on no shipped firmware.
    for override in overrides.values():
        for key, value in override.items():
            current = base.get(key)
            base[key] = value if current is None else min(current, value)

    if firmware is None:
        reason = "conservative limit across all known firmware (no firmware declared)"
    else:
        reason = (
            f"conservative limit across all known firmware "
            f"(firmware {firmware} is not declared in firmware_limits)"
        )
    return base, reason


def _check_name_length(
    report: ValidationReport,
    limits: Mapping[str, int],
    limit_key: str,
    kind: str,
    name: str,
) -> None:
    """Record a critical issue when a name exceeds the radio's storage.

    Skipped when the capability is absent, so an incomplete capabilities file
    degrades to today's behavior instead of producing false failures.
    """

    limit = limits.get(limit_key)
    if limit is None:
        return
    if len(name) > limit:
        report.critical(
            (),
            f"{kind} name '{name}' is {len(name)} characters; "
            f"radio limit is {limit}",
        )


def _check_group_policy(
    report: ValidationReport,
    profile: Mapping[str, Any],
    limits: Mapping[str, int],
    limit_source: str,
    contacts: Mapping[str, Any],
    channel_counts: Mapping[str, int],
    ambiguous_assignments: set[str],
    selected: set[str],
) -> None:
    """Validate profile rx_groups, scan_lists, and per-assignment references."""

    rx_groups = profile.get("rx_groups", [])
    scan_lists = profile.get("scan_lists", [])

    max_rx_groups = limits.get("max_rx_group_lists")
    if max_rx_groups is not None and len(rx_groups) > max_rx_groups:
        report.critical(
            (),
            f"profile has {len(rx_groups)} RX group lists; "
            f"{limit_source} is {max_rx_groups}",
        )

    def _check_contact(context: str, contact_id: str, *, require_group: bool) -> None:
        contact = contacts.get(contact_id)
        if contact is None:
            report.critical(
                (), f"{context} references unknown SSRF contact '{contact_id}'"
            )
            return
        kind = _contact_kind(contact.kind)
        if kind is None:
            report.critical(
                (),
                f"{context} references contact '{contact_id}' with "
                f"unrecognized kind '{contact.kind}'",
            )
        elif require_group and kind != "group":
            report.critical(
                (),
                f"{context} references contact '{contact_id}' of kind "
                f"'{contact.kind}'; RX group lists hold group calls only",
            )
        if contact.number is None:
            report.critical(
                (),
                f"{context} references contact '{contact_id}', "
                "which has no DMR number",
            )
        _check_name_length(
            report, limits, "max_contact_name_chars", "contact", contact.name
        )

    rx_group_ids: set[str] = set()
    for group in rx_groups:
        if group["id"] in rx_group_ids:
            report.critical((), f"duplicate RX group list ID '{group['id']}'")
        rx_group_ids.add(group["id"])
        context = f"RX group list '{group['id']}'"
        max_members = limits.get("max_talkgroups_per_rx_group_list")
        if max_members is not None and len(group["contacts"]) > max_members:
            report.critical(
                (),
                f"{context} has {len(group['contacts'])} talkgroups; "
                f"{limit_source} is {max_members}",
            )
        for contact_id in group["contacts"]:
            _check_contact(context, contact_id, require_group=True)

    max_scan_lists = limits.get("max_scan_lists")
    if max_scan_lists is not None and len(scan_lists) > max_scan_lists:
        report.critical(
            (),
            f"profile has {len(scan_lists)} scan lists; "
            f"{limit_source} is {max_scan_lists}",
        )

    scan_list_ids: set[str] = set()
    for scan_list in scan_lists:
        if scan_list["id"] in scan_list_ids:
            report.critical((), f"duplicate scan list ID '{scan_list['id']}'")
        scan_list_ids.add(scan_list["id"])
        context = f"scan list '{scan_list['id']}'"
        _check_name_length(
            report, limits, "max_scan_list_name_chars", "scan list", scan_list["name"]
        )
        channel_count = 0
        for assignment_id in scan_list["channels"]:
            if assignment_id in ambiguous_assignments:
                report.critical(
                    (),
                    f"{context} references ambiguous assignment "
                    f"'{assignment_id}'",
                )
                continue
            if assignment_id not in channel_counts:
                report.critical(
                    (),
                    f"{context} references unknown assignment "
                    f"'{assignment_id}'",
                )
                continue
            if assignment_id not in selected:
                report.critical(
                    (),
                    f"{context} references assignment '{assignment_id}', "
                    "which no zone selects",
                )
                continue
            channel_count += channel_counts[assignment_id]
        max_members = limits.get("max_channels_per_scan_list")
        if max_members is not None and channel_count > max_members:
            report.critical(
                (),
                f"{context} expands to {channel_count} channels; "
                f"{limit_source} is {max_members}",
            )

    for zone in profile["zones"]:
        for assignment in zone["assignments"]:
            if not isinstance(assignment, dict):
                continue
            context = f"assignment '{assignment['id']}'"
            contact_id = assignment.get("contact")
            if contact_id is not None:
                _check_contact(context, contact_id, require_group=False)
            rx_group = assignment.get("rx_group")
            if rx_group is not None and rx_group not in rx_group_ids:
                report.critical(
                    (),
                    f"{context} references unknown RX group list '{rx_group}'",
                )
            scan_list = assignment.get("scan_list")
            if scan_list is not None and scan_list not in scan_list_ids:
                report.critical(
                    (),
                    f"{context} references unknown scan list '{scan_list}'",
                )


def _band_label(band: Mapping[str, Any]) -> str:
    name = band.get("name")
    span = f"{band['min_mhz']}-{band['max_mhz']} MHz"
    return f"{name} ({span})" if name else span


def _frequency_in_bands(
    freq_mhz: float, bands: Sequence[Mapping[str, Any]], *, transmit: bool
) -> bool:
    """True when the frequency falls inside any usable band.

    A band marked ``rx_only`` satisfies receive checks but never transmit
    checks.
    """

    for band in bands:
        if transmit and band.get("rx_only", False):
            continue
        if band["min_mhz"] <= freq_mhz <= band["max_mhz"]:
            return True
    return False


def _check_radio_support(
    report: ValidationReport,
    capabilities: Mapping[str, Any],
    documents: Sequence[Any],
    selected: set[str],
) -> None:
    """Verify selected channels are physically usable on the target radio.

    Each check is skipped when the corresponding capability is absent, so an
    incomplete capabilities file degrades to today's behavior instead of
    producing false failures.
    """

    bands = capabilities.get("bands")
    modes = capabilities.get("modes")
    bandwidths = capabilities.get("bandwidths_khz")
    if not (bands or modes or bandwidths):
        return

    radio_name = capabilities["name"]
    mode_set = {str(mode).upper() for mode in modes} if modes else None

    for document in documents:
        reference = document.reference
        chains = {chain.id: chain for chain in reference.rf_chains}
        for assignment in reference.assignments:
            if assignment.id not in selected:
                continue
            chain = chains.get(assignment.rf_chain_id)
            if chain is None:
                continue
            label = assignment.channel_name or assignment.id

            if bands:
                endpoints = []
                if getattr(chain, "rx", None) is not None:
                    endpoints.append(("RX", chain.rx.freq_mhz, False))
                if getattr(chain, "tx", None) is not None:
                    endpoints.append(("TX", chain.tx.freq_mhz, True))
                for direction, freq_mhz, transmit in endpoints:
                    if freq_mhz is None:
                        continue
                    if not _frequency_in_bands(freq_mhz, bands, transmit=transmit):
                        supported = ", ".join(_band_label(band) for band in bands)
                        report.critical(
                            (f"channel '{label}'",),
                            f"{direction} {freq_mhz} MHz is "
                            f"outside the bands supported by {radio_name} "
                            f"({supported})",
                        )

            mode = getattr(getattr(chain, "mode", None), "type", None)
            if mode_set and mode and str(mode).upper() not in mode_set:
                report.critical(
                    (f"channel '{label}'",),
                    f"uses mode {mode}, which "
                    f"{radio_name} does not support "
                    f"({', '.join(sorted(mode_set))})",
                )

            if bandwidths and getattr(chain, "tx", None) is not None:
                # Validate the bandwidth the exporters will actually use, which
                # may be derived from the ITU emission designator when the SSRF
                # data carries no explicit bandwidth_khz.
                bandwidth_khz = getattr(chain.tx, "bandwidth_khz", None)
                if bandwidth_khz is None:
                    bandwidth_khz = bandwidth_khz_from_emission(
                        getattr(chain.tx, "emission", None)
                    )
                if bandwidth_khz is not None and not any(
                    abs(bandwidth_khz - supported) < 1e-6 for supported in bandwidths
                ):
                    allowed = ", ".join(str(value) for value in bandwidths)
                    report.critical(
                        (f"channel '{label}'",),
                        f"uses {bandwidth_khz} kHz bandwidth, "
                        f"which {radio_name} does not support ({allowed} kHz)",
                    )


def _load_capabilities(
    radio_id: str,
    radio_root: Path = DEFAULT_RADIO_ROOT,
) -> dict[str, Any]:
    """Load and schema-validate a radio's capabilities document."""

    radio_path = radio_root / radio_id / "capabilities.json"
    if not radio_path.is_file():
        raise ProfileValidationError(f"unknown radio '{radio_id}'")
    capabilities = json.loads(radio_path.read_text(encoding="utf-8"))
    if DEFAULT_CAPABILITIES_SCHEMA_PATH.is_file():
        capabilities_schema = json.loads(
            DEFAULT_CAPABILITIES_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        capability_errors = sorted(
            Draft202012Validator(capabilities_schema).iter_errors(capabilities),
            key=lambda error: list(error.path),
        )
        if capability_errors:
            raise ProfileValidationError(
                f"{radio_path}: {_format_schema_errors(capability_errors)}"
            )
    return capabilities


def _load_instance_registry(
    registry_path: Path,
    *,
    schema_path: Path = DEFAULT_INSTANCE_REGISTRY_SCHEMA_PATH,
) -> dict[str, Any]:
    """Load and schema-validate an instance registry document."""

    registry = _load_mapping(registry_path)
    if schema_path.is_file():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema).iter_errors(registry),
            key=lambda error: list(error.path),
        )
        if errors:
            raise ProfileValidationError(
                f"{registry_path}: {_format_schema_errors(errors)}"
            )
    return registry


def _validate_instance_reference(
    profile: Mapping[str, Any],
    registry_path: Path,
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Ensure the profile's radio_instance resolves to matching registry data."""

    instances = registry.get("instances", {})
    instance_id = profile.get("radio_instance", profile["id"])
    if instance_id not in instances:
        raise ProfileValidationError(
            f"{registry_path}: missing instance '{instance_id}'"
        )

    instance = instances[instance_id]
    instance_radio = instance.get("radio")
    if instance_radio != profile["radio"]:
        raise ProfileValidationError(
            f"{registry_path}: instance '{instance_id}' targets radio "
            f"'{instance_radio}', but profile targets '{profile['radio']}'"
        )
    return dict(instance)


def _load_and_validate_profile(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    instance_registry_path: Path | None = None,
) -> tuple[dict[str, Any], Sequence[Any], dict[str, Any] | None, ValidationReport]:
    """Load a profile and its validated, overlay-resolved SSRF documents.

    Raises on the first structural failure (unreadable input, schema error,
    registry mismatch). Limit and radio-support findings are collected into a
    severity-graded report instead, so one failed run surfaces every critical
    issue at once; the report also carries non-fatal hints and warnings.
    """

    profile = _load_profile_with_inheritance(profile_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_errors = sorted(
        Draft202012Validator(schema).iter_errors(profile),
        key=lambda error: list(error.path),
    )
    if schema_errors:
        raise ProfileValidationError(
            f"{profile_path}: {_format_schema_errors(schema_errors)}"
        )

    capabilities = _load_capabilities(profile["radio"], radio_root)
    instance_metadata: dict[str, Any] | None = None
    if instance_registry_path is not None:
        registry = _load_instance_registry(instance_registry_path)
        instance_metadata = _validate_instance_reference(
            profile,
            instance_registry_path,
            registry,
        )

    try:
        from ssrf import resolve_ssrf_roots
    except ImportError as exc:
        raise ProfileValidationError(
            "SSRF-Lite with overlay support must be installed"
        ) from exc

    documents = resolve_ssrf_roots(ssrf_roots)
    channel_counts, ambiguous_assignments = _assignment_channel_counts(documents)
    zones = profile["zones"]
    firmware = None
    if instance_metadata is not None:
        firmware = instance_metadata.get("firmware")
    limits, limit_source = _resolve_firmware_limits(capabilities, firmware)

    report = ValidationReport()
    if "conservative" in limit_source:
        report.hint((), f"validating against {limit_source}")

    if len(zones) > limits["max_zones"]:
        report.critical(
            (),
            f"profile has {len(zones)} zones; "
            f"{limit_source} is {limits['max_zones']}",
        )

    zone_ids: set[str] = set()
    selected: set[str] = set()
    total_channels = 0
    for zone in zones:
        if zone["id"] in zone_ids:
            report.critical((), f"duplicate zone ID '{zone['id']}'")
        zone_ids.add(zone["id"])
        _check_name_length(report, limits, "max_zone_name_chars", "zone", zone["name"])
        zone_channel_count = 0
        for assignment in zone["assignments"]:
            assignment_id = _assignment_id(assignment)
            if assignment_id in ambiguous_assignments:
                report.critical(
                    (),
                    f"zone '{zone['name']}' references ambiguous assignment "
                    f"'{assignment_id}'",
                )
                continue
            if assignment_id not in channel_counts:
                report.critical(
                    (),
                    f"zone '{zone['name']}' references unknown assignment "
                    f"'{assignment_id}'",
                )
                continue
            if assignment_id in selected:
                report.critical(
                    (),
                    f"assignment '{assignment_id}' is selected more than once",
                )
                continue
            selected.add(assignment_id)
            zone_channel_count += channel_counts[assignment_id]
        if zone_channel_count > limits["max_channels_per_zone"]:
            report.critical(
                (),
                f"zone '{zone['name']}' expands to {zone_channel_count} channels; "
                f"{limit_source} is {limits['max_channels_per_zone']}",
            )
        total_channels += zone_channel_count

    if total_channels > limits["max_channels"]:
        report.critical(
            (),
            f"profile expands to {total_channels} channels; "
            f"{limit_source} is {limits['max_channels']}",
        )

    _check_group_policy(
        report,
        profile,
        limits,
        limit_source,
        _ssrf_contacts(documents),
        channel_counts,
        ambiguous_assignments,
        selected,
    )
    _check_radio_support(report, capabilities, documents, selected)
    if report.has_critical:
        raise ProfileValidationError(report.critical_message())
    return profile, documents, instance_metadata, report


def load_and_validate_profile(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    instance_registry_path: Path | None = None,
) -> dict[str, Any]:
    """Load a profile and validate its schema, references, and radio limits."""

    profile, _, _, _ = _load_and_validate_profile(
        profile_path,
        ssrf_roots,
        schema_path=schema_path,
        radio_root=radio_root,
        instance_registry_path=instance_registry_path,
    )
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument(
        "--ssrf-root",
        type=Path,
        action="append",
        required=True,
        help="SSRF root in precedence order; repeat for overlays",
    )
    parser.add_argument("--radio-root", type=Path, default=DEFAULT_RADIO_ROOT)
    parser.add_argument(
        "--instance-registry",
        type=Path,
        default=None,
        help="optional path to instance registry file",
    )
    parser.add_argument(
        "--output-format",
        choices=(
            "summary",
            "json",
            "yaml",
            "chirp-csv",
            "qdmr-yaml",
            "html",
            "markdown",
        ),
        default="summary",
        help="inspection output format (default: summary)",
    )
    parser.add_argument(
        "--print-merged-profile",
        action="store_true",
        help="print the inheritance-resolved profile as YAML",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="write .artifacts/<radio>/<instance> outputs under this directory",
    )
    args = parser.parse_args()

    try:
        if args.print_merged_profile:
            profile, _, _, report = _load_and_validate_profile(
                args.profile,
                args.ssrf_root,
                radio_root=args.radio_root,
                instance_registry_path=args.instance_registry,
            )
            for issue in report.non_critical():
                print(
                    f"{issue.severity.name.lower()}: {issue.format()}",
                    file=sys.stderr,
                )
            print(yaml.safe_dump(profile, sort_keys=False), end="")
            return 0
        if args.output_format == "summary":
            profile, _, _, report = _load_and_validate_profile(
                args.profile,
                args.ssrf_root,
                radio_root=args.radio_root,
                instance_registry_path=args.instance_registry,
            )
            for issue in report.non_critical():
                print(
                    f"{issue.severity.name.lower()}: {issue.format()}",
                    file=sys.stderr,
                )
        else:
            from .resolved import resolve_codeplug

            codeplug = resolve_codeplug(
                args.profile,
                args.ssrf_root,
                radio_root=args.radio_root,
                instance_registry_path=args.instance_registry,
            )
    except (OSError, ProfileValidationError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    render_options: dict[str, Any] = {}
    if args.output_format != "summary" and (
        args.output_format in ("html", "markdown") or args.artifact_root is not None
    ):
        render_options["radio_name"] = _load_capabilities(
            codeplug.radio_id, args.radio_root
        ).get("name")
        if args.instance_registry is not None:
            render_options["fleet_instances"] = _load_instance_registry(
                args.instance_registry
            )["instances"]
    if args.artifact_root is not None and args.output_format != "summary":
        from .artifacts import write_profile_artifacts

        reference_path, _ = write_profile_artifacts(
            args.artifact_root, codeplug, **render_options
        )
        if args.output_format == "html":
            print(reference_path, end="\n")
            return 0
    if args.output_format == "json":
        print(codeplug.to_json(), end="")
        return 0
    if args.output_format == "yaml":
        print(codeplug.to_yaml(), end="")
        return 0
    if args.output_format == "chirp-csv":
        from .exporters.chirp_csv import chirp_csv_from_resolved

        print(chirp_csv_from_resolved(codeplug), end="")
        return 0
    if args.output_format == "qdmr-yaml":
        from .exporters.qdmr_yaml import qdmr_yaml_from_resolved

        fleet_instances = None
        if args.instance_registry is not None:
            fleet_instances = _load_instance_registry(args.instance_registry)[
                "instances"
            ]
        print(
            qdmr_yaml_from_resolved(codeplug, fleet_instances=fleet_instances),
            end="",
        )
        return 0
    if args.output_format == "html":
        from .artifacts import html_reference_from_resolved

        print(html_reference_from_resolved(codeplug, **render_options), end="")
        return 0
    if args.output_format == "markdown":
        from .artifacts import markdown_reference_from_resolved

        print(markdown_reference_from_resolved(codeplug, **render_options), end="")
        return 0
    assignment_count = sum(len(zone["assignments"]) for zone in profile["zones"])
    print(
        f"Validated profile '{profile['id']}': "
        f"{len(profile['zones'])} zones, {assignment_count} assignments"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())