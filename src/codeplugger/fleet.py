"""Plan codeplug generation for every radio in an instance registry."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import yaml

from .profile import (
    DEFAULT_INSTANCE_REGISTRY_SCHEMA_PATH,
    DEFAULT_RADIO_ROOT,
    ProfileValidationError,
    _load_instance_registry,
)
from .resolved import resolve_codeplug


@dataclass(frozen=True)
class FleetRadioPlan:
    """The dry-run result for one physical radio."""

    id: str
    radio: str
    profile: str
    label: str | None
    status: str
    channel_count: int
    zone_count: int
    resolved_sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class FleetPlan:
    """A complete, non-programming fleet plan."""

    radios: tuple[FleetRadioPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"radios": [asdict(radio) for radio in self.radios]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @property
    def is_valid(self) -> bool:
        return all(radio.status == "ready" for radio in self.radios)


def _profile_paths(profiles_root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in sorted(profiles_root.rglob("*.yml")):
        try:
            profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ProfileValidationError(f"{path}: could not read profile: {exc}") from exc
        profile_id = profile.get("id") if isinstance(profile, dict) else None
        if not isinstance(profile_id, str) or not profile_id:
            raise ProfileValidationError(f"{path}: profile has no valid id")
        if profile_id in paths:
            raise ProfileValidationError(f"duplicate profile ID '{profile_id}'")
        paths[profile_id] = path
    return paths


def plan_registry(
    instance_registry_path: Path,
    profiles_root: Path,
    ssrf_roots: Sequence[Path],
    *,
    radio_root: Path = DEFAULT_RADIO_ROOT,
    registry_schema_path: Path = DEFAULT_INSTANCE_REGISTRY_SCHEMA_PATH,
) -> FleetPlan:
    """Resolve each registry instance using its desired profile."""

    registry = _load_instance_registry(
        instance_registry_path,
        schema_path=registry_schema_path,
    )
    profile_paths = _profile_paths(profiles_root)
    plans: list[FleetRadioPlan] = []
    for instance_id, instance in registry["instances"].items():
        profile_id = instance.get("profile")
        base = dict(
            id=instance_id,
            radio=instance["radio"],
            profile=str(profile_id or ""),
            label=instance.get("label"),
        )
        try:
            if not profile_id:
                raise ProfileValidationError(
                    f"instance '{instance_id}' has no desired profile"
                )
            profile_path = profile_paths.get(profile_id)
            if profile_path is None:
                raise ProfileValidationError(
                    f"profile '{profile_id}' was not found under {profiles_root}"
                )
            resolved = resolve_codeplug(
                profile_path,
                ssrf_roots,
                radio_root=radio_root,
                instance_registry_path=instance_registry_path,
            )
            if resolved.radio_id != instance["radio"]:
                raise ProfileValidationError(
                    f"profile '{profile_id}' targets radio '{resolved.radio_id}', "
                    f"registry expects '{instance['radio']}'"
                )
            if resolved.radio_instance_id != instance_id:
                raise ProfileValidationError(
                    f"profile '{profile_id}' resolves instance "
                    f"'{resolved.radio_instance_id}', registry expects '{instance_id}'"
                )
            digest = hashlib.sha256(resolved.to_json().encode("utf-8")).hexdigest()
            plans.append(
                FleetRadioPlan(
                    **base,
                    status="ready",
                    channel_count=len(resolved.channels),
                    zone_count=len(resolved.zones),
                    resolved_sha256=digest,
                )
            )
        except (OSError, ProfileValidationError, ValueError) as exc:
            plans.append(
                FleetRadioPlan(
                    **base,
                    status="error",
                    channel_count=0,
                    zone_count=0,
                    error=str(exc),
                )
            )
    return FleetPlan(radios=tuple(plans))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-registry", type=Path, required=True)
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument(
        "--ssrf-root",
        type=Path,
        action="append",
        required=True,
        help="SSRF root in precedence order; repeat for overlays",
    )
    parser.add_argument("--radio-root", type=Path, default=DEFAULT_RADIO_ROOT)
    parser.add_argument("--output-format", choices=("summary", "json"), default="summary")
    args = parser.parse_args()
    try:
        plan = plan_registry(
            args.instance_registry,
            args.profiles_root,
            args.ssrf_root,
            radio_root=args.radio_root,
        )
    except (OSError, ProfileValidationError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    if args.output_format == "json":
        print(plan.to_json(), end="")
    else:
        print(f"Fleet plan: {len(plan.radios)} radios")
        for radio in plan.radios:
            detail = (
                f"{radio.channel_count} channels, {radio.zone_count} zones"
                if radio.status == "ready"
                else radio.error
            )
            print(f"{radio.status}: {radio.id} ({radio.profile}) - {detail}")
    return 0 if plan.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())