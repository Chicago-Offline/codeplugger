"""Validate Codeplugger profiles against schema, radio, and SSRF inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
import yaml

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
                bandwidth_khz = getattr(chain.tx, "bandwidth_khz", None)
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

    profile = _load_mapping(profile_path)
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
        choices=("summary", "json", "yaml", "chirp-csv", "qdmr-yaml", "html"),
        default="summary",
        help="inspection output format (default: summary)",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="write .artifacts/<radio>/<instance> outputs under this directory",
    )
    args = parser.parse_args()

    try:
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
        args.output_format == "html" or args.artifact_root is not None
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
    assignment_count = sum(len(zone["assignments"]) for zone in profile["zones"])
    print(
        f"Validated profile '{profile['id']}': "
        f"{len(profile['zones'])} zones, {assignment_count} assignments"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())