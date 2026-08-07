"""Validate Codeplugger profiles against schema, radio, and SSRF inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "profile-0.1.schema.json"
DEFAULT_CAPABILITIES_SCHEMA_PATH = (
    PROJECT_ROOT / "schemas" / "capabilities-0.2.schema.json"
)
DEFAULT_RADIO_ROOT = PROJECT_ROOT / "radios"


class ProfileValidationError(ValueError):
    """Raised when a profile cannot be resolved into a valid selection."""


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
                        raise ProfileValidationError(
                            f"channel '{label}' {direction} {freq_mhz} MHz is "
                            f"outside the bands supported by {radio_name} "
                            f"({supported})"
                        )

            mode = getattr(getattr(chain, "mode", None), "type", None)
            if mode_set and mode and str(mode).upper() not in mode_set:
                raise ProfileValidationError(
                    f"channel '{label}' uses mode {mode}, which "
                    f"{radio_name} does not support "
                    f"({', '.join(sorted(mode_set))})"
                )

            if bandwidths and getattr(chain, "tx", None) is not None:
                bandwidth_khz = getattr(chain.tx, "bandwidth_khz", None)
                if bandwidth_khz is not None and not any(
                    abs(bandwidth_khz - supported) < 1e-6 for supported in bandwidths
                ):
                    allowed = ", ".join(str(value) for value in bandwidths)
                    raise ProfileValidationError(
                        f"channel '{label}' uses {bandwidth_khz} kHz bandwidth, "
                        f"which {radio_name} does not support ({allowed} kHz)"
                    )


def _load_and_validate_profile(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
) -> tuple[dict[str, Any], Sequence[Any]]:
    """Load a profile and its validated, overlay-resolved SSRF documents."""

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

    radio_id = profile["radio"]
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

    try:
        from ssrf import resolve_ssrf_roots
    except ImportError as exc:
        raise ProfileValidationError(
            "SSRF-Lite with overlay support must be installed"
        ) from exc

    documents = resolve_ssrf_roots(ssrf_roots)
    channel_counts, ambiguous_assignments = _assignment_channel_counts(documents)
    zones = profile["zones"]
    limits: Mapping[str, int] = capabilities["limits"]

    if len(zones) > limits["max_zones"]:
        raise ProfileValidationError(
            f"profile has {len(zones)} zones; radio limit is {limits['max_zones']}"
        )

    zone_ids: set[str] = set()
    selected: set[str] = set()
    total_channels = 0
    for zone in zones:
        if zone["id"] in zone_ids:
            raise ProfileValidationError(f"duplicate zone ID '{zone['id']}'")
        zone_ids.add(zone["id"])
        zone_channel_count = 0
        for assignment_id in zone["assignments"]:
            if assignment_id in ambiguous_assignments:
                raise ProfileValidationError(
                    f"zone '{zone['name']}' references ambiguous assignment "
                    f"'{assignment_id}'"
                )
            if assignment_id not in channel_counts:
                raise ProfileValidationError(
                    f"zone '{zone['name']}' references unknown assignment "
                    f"'{assignment_id}'"
                )
            if assignment_id in selected:
                raise ProfileValidationError(
                    f"assignment '{assignment_id}' is selected more than once"
                )
            selected.add(assignment_id)
            zone_channel_count += channel_counts[assignment_id]
        if zone_channel_count > limits["max_channels_per_zone"]:
            raise ProfileValidationError(
                f"zone '{zone['name']}' expands to {zone_channel_count} channels; "
                f"radio limit is {limits['max_channels_per_zone']}"
            )
        total_channels += zone_channel_count

    if total_channels > limits["max_channels"]:
        raise ProfileValidationError(
            f"profile expands to {total_channels} channels; "
            f"radio limit is {limits['max_channels']}"
        )

    _check_radio_support(capabilities, documents, selected)
    return profile, documents


def load_and_validate_profile(
    profile_path: Path,
    ssrf_roots: Sequence[Path],
    *,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    radio_root: Path = DEFAULT_RADIO_ROOT,
) -> dict[str, Any]:
    """Load a profile and validate its schema, references, and radio limits."""

    profile, _ = _load_and_validate_profile(
        profile_path,
        ssrf_roots,
        schema_path=schema_path,
        radio_root=radio_root,
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
        "--output-format",
        choices=("summary", "json", "yaml"),
        default="summary",
        help="inspection output format (default: summary)",
    )
    args = parser.parse_args()

    try:
        if args.output_format == "summary":
            profile = load_and_validate_profile(
                args.profile,
                args.ssrf_root,
                radio_root=args.radio_root,
            )
        else:
            from .resolved import resolve_codeplug

            codeplug = resolve_codeplug(
                args.profile,
                args.ssrf_root,
                radio_root=args.radio_root,
            )
    except (OSError, ProfileValidationError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    if args.output_format == "json":
        print(codeplug.to_json(), end="")
        return 0
    if args.output_format == "yaml":
        print(codeplug.to_yaml(), end="")
        return 0
    assignment_count = sum(len(zone["assignments"]) for zone in profile["zones"])
    print(
        f"Validated profile '{profile['id']}': "
        f"{len(profile['zones'])} zones, {assignment_count} assignments"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())