"""The front door: pick a radio, pick a use case, get a programmed radio.

``codeplugger-profile`` and ``codeplugger-fleet`` serve someone who already
owns profiles. This CLI serves the step before that -- an unopened radio and
an intention ("make this a GMRS handheld") -- and it does the whole job:
discover what is supported, fit the use case to the radio, export the file the
radio's toolchain wants, and, where a gated backend exists, write it.

Nothing here invents RF policy. Everything is compiled from the same SSRF data
and validated against the same capabilities as a hand-written profile, and
every write goes through the backend's existing confirm / backup / read-back
gates rather than around them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactStore, write_html_reference, write_markdown_reference
from .catalog import (
    DEFAULT_CACHE_ROOT,
    DEFAULT_CHECKOUT_ROOT,
    DEFAULT_SOURCES_PATH,
    DEFAULT_USE_CASE_ROOT,
    ProgrammingPath,
    Radio,
    available_profile_sources,
    available_radios,
    available_use_cases,
    find_radio,
    find_shared_profile,
    find_use_case,
    looks_like_path,
    programming_path,
    repo_revision,
    source_profiles,
)
from .fitting import FittedProfile, fit_use_case
from .profile import (
    DEFAULT_RADIO_ROOT,
    ProfileValidationError,
    _load_and_validate_profile,
    _load_instance_registry,
    _validate_profile,
    default_ssrf_roots,
)
from .resolved import ResolvedCodeplug, build_codeplug
from .validation import ValidationReport


class CommandError(RuntimeError):
    """A failure that should reach the operator as a message, not a traceback."""


@dataclass(frozen=True)
class Build:
    """Everything one `build` produced, and where it landed."""

    radio: Radio
    codeplug: ResolvedCodeplug
    path: ProgrammingPath
    export_path: Path
    reference_path: Path
    store: ArtifactStore
    fitted: FittedProfile | None


def _timestamp() -> str:
    """A filename-safe UTC stamp, so backups never collide or overwrite."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ssrf_documents(ssrf_roots: Sequence[Path]) -> Sequence[Any]:
    try:
        from ssrf import resolve_ssrf_roots
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise CommandError("SSRF-Lite with overlay support must be installed") from exc
    return resolve_ssrf_roots(list(ssrf_roots))


# -- discovery ------------------------------------------------------------


def _command_radios(args: argparse.Namespace) -> int:
    radios = available_radios(args.radio_root)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": radio.id,
                        "name": radio.name,
                        "modes": list(radio.modes),
                        "max_channels": radio.max_channels,
                        "max_zones": radio.max_zones,
                        "export_format": programming_path(
                            radio.capabilities
                        ).export_format,
                        "backend": programming_path(radio.capabilities).backend,
                    }
                    for radio in radios
                ],
                indent=2,
            )
        )
        return 0
    width = max(len(radio.id) for radio in radios)
    for radio in radios:
        path = programming_path(radio.capabilities)
        writes = path.backend or "export only"
        print(
            f"{radio.id.ljust(width)}  {radio.name}"
            f"  [{'/'.join(radio.modes) or 'modes unknown'}"
            f", {radio.max_channels} ch, writes: {writes}]"
        )
    return 0


def _command_use_cases(args: argparse.Namespace) -> int:
    use_cases = available_use_cases(args.use_case_root)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": use_case.id,
                        "name": use_case.name,
                        "zones": [
                            {"id": zone_id, "name": name}
                            for zone_id, name in use_case.zones
                        ],
                    }
                    for use_case in use_cases
                ],
                indent=2,
            )
        )
        return 0
    width = max(len(use_case.id) for use_case in use_cases)
    for use_case in use_cases:
        zones = ", ".join(name for _, name in use_case.zones)
        print(f"{use_case.id.ljust(width)}  {use_case.name}")
        print(f"{' ' * width}  zones: {zones}")
    return 0


def _command_profiles(args: argparse.Namespace) -> int:
    """List the community profile collections and what each covers."""

    sources = available_profile_sources(args.sources)
    listing: list[dict[str, Any]] = []
    for source in sources:
        entry: dict[str, Any] = {
            "id": source.id,
            "name": source.name,
            "repo": source.repo,
            "root": str(source.root(args.checkout_root)),
            "available": source.is_available(args.checkout_root),
            "profiles": [],
        }
        if entry["available"]:
            try:
                entry["profiles"] = [
                    {"id": profile.id, "radio": profile.radio, "name": profile.name}
                    for profile in source_profiles(source, args.checkout_root)
                ]
            except ProfileValidationError as exc:
                entry["error"] = str(exc)
        listing.append(entry)

    if args.json:
        print(json.dumps(listing, indent=2))
        return 0

    for entry in listing:
        print(f"{entry['id']}  {entry['name']}")
        if not entry["available"]:
            clone = (
                f"git clone {entry['repo']} {entry['root']}"
                if entry["repo"]
                else entry["root"]
            )
            print(f"  not checked out -- {clone}")
            continue
        if "error" in entry:
            print(f"  unusable -- {entry['error']}")
            continue
        for profile in entry["profiles"]:
            print(f"  {profile['radio']}  {profile['id']}  ({profile['name']})")
    return 0


# -- build ----------------------------------------------------------------


def _report_issues(report: ValidationReport) -> None:
    for issue in report.non_critical():
        print(f"{issue.severity.name.lower()}: {issue.format()}", file=sys.stderr)


def _accept_drops(fitted: FittedProfile, *, accept: bool) -> None:
    """Show what the radio could not take and make a human agree to it.

    Auto-fitting is a convenience, not a decision the tool gets to make
    quietly: an operator who asked for GMRS and receives 15 of its 29 channels
    has to know that before the radio does.
    """

    if not fitted.drops:
        return
    print("This radio cannot hold the whole use case:", file=sys.stderr)
    for drop in fitted.drops:
        print(f"  - {drop.format()}", file=sys.stderr)
    print(
        f"Keeping {fitted.channel_count} channel(s) in "
        f"{fitted.zone_count} zone(s).",
        file=sys.stderr,
    )
    if accept:
        return
    if not sys.stdin.isatty():
        raise CommandError(
            "refusing to continue with a trimmed use case; re-run with "
            "--accept-fit, or choose what to keep with --zones"
        )
    answer = input("Continue with the trimmed codeplug? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise CommandError("cancelled")


def _render_export(
    path: ProgrammingPath,
    codeplug: ResolvedCodeplug,
    capabilities: Mapping[str, Any],
    *,
    baseline: Path | None,
    fleet_instances: Mapping[str, Any] | None,
) -> str:
    if path.export_format == "chirp-csv":
        from .exporters.chirp_csv import chirp_csv_from_resolved

        return chirp_csv_from_resolved(codeplug)
    if path.export_format == "qdmr-yaml":
        from .exporters.qdmr_yaml import qdmr_yaml_from_resolved

        return qdmr_yaml_from_resolved(
            codeplug,
            fleet_instances=fleet_instances,
            zone_banks=int(capabilities.get("zone_banks", 1)),
        )
    if path.export_format == "benlink-plan":
        from .exporters.benlink_plan import benlink_plan_json_from_resolved

        return benlink_plan_json_from_resolved(
            codeplug,
            capabilities=dict(capabilities),
            generated_by="codeplugger",
        )
    if path.export_format == "p64-toml":
        from .exporters.p64_toml import p64_toml_from_resolved

        if baseline is None:
            raise CommandError(
                "this radio exports by editing a decoded baseline; pass "
                "--baseline with the output of `p64tool decode` for the radio "
                "you are about to program"
            )
        return p64_toml_from_resolved(
            codeplug,
            baseline.read_text(encoding="utf-8"),
            fleet_instances=fleet_instances,
        )
    raise CommandError(f"no exporter for format '{path.export_format}'")


def _resolve_profile_argument(args: argparse.Namespace) -> tuple[Path, list[Path]]:
    """Turn ``--profile`` into a file plus the SSRF roots it needs.

    It takes a path, the name of a registered collection, a directory, or a git
    URL to clone. Anything but a plain file brings its own SSRF roots -- a
    community profile usually selects from an overlay as well as the national
    data -- unless --ssrf-root overrides them.
    """

    value = str(args.profile)
    if looks_like_path(value) and not Path(value).is_dir():
        return Path(value), default_ssrf_roots(args.ssrf_root)

    shared = find_shared_profile(
        value,
        radio_id=args.radio,
        sources_path=args.sources,
        checkout_root=args.checkout_root,
        cache_root=args.cache_root,
        refresh=args.refresh,
    )
    revision = repo_revision(shared.source.root(args.checkout_root))
    at = f" at {revision}" if revision else ""
    print(f"Using {shared.id} from {shared.source.name}{at} ({shared.path})")
    roots = (
        default_ssrf_roots(args.ssrf_root)
        if args.ssrf_root
        else list(shared.ssrf_roots) or default_ssrf_roots(None)
    )
    return shared.path, roots


def _build(args: argparse.Namespace) -> Build:
    """Resolve the requested codeplug and write its files, without a radio."""

    ssrf_roots = default_ssrf_roots(args.ssrf_root)
    report = ValidationReport()
    fitted: FittedProfile | None = None

    if args.profile is not None:
        profile_path, ssrf_roots = _resolve_profile_argument(args)
        profile, documents, instance_metadata, load_report = _load_and_validate_profile(
            profile_path,
            ssrf_roots,
            radio_root=args.radio_root,
            instance_registry_path=args.instance_registry,
            radio_instance=args.instance,
        )
        if args.radio and profile["radio"] != args.radio:
            raise CommandError(
                f"{profile_path} is a profile for '{profile['radio']}', not "
                f"'{args.radio}'"
            )
        radio = find_radio(str(profile["radio"]), args.radio_root)
    else:
        radio = find_radio(args.radio, args.radio_root)
        use_case = find_use_case(args.use_case, args.use_case_root)
        documents = _ssrf_documents(ssrf_roots)
        fitted = fit_use_case(use_case, radio, documents, zones=args.zones)
        _accept_drops(fitted, accept=args.accept_fit)
        profile, documents, instance_metadata, load_report = _validate_profile(
            fitted.profile,
            ssrf_roots,
            source=f"use case '{use_case.id}' on {radio.name}",
            radio_root=args.radio_root,
            instance_registry_path=args.instance_registry,
            radio_instance=args.instance,
        )

    report.extend(load_report)
    codeplug = build_codeplug(
        profile,
        documents,
        instance_metadata,
        radio_root=args.radio_root,
        report=report,
    )
    _report_issues(report)

    path = programming_path(radio.capabilities)
    fleet_instances = None
    if args.instance_registry is not None:
        fleet_instances = _load_instance_registry(args.instance_registry)["instances"]
    export = _render_export(
        path,
        codeplug,
        radio.capabilities,
        baseline=args.baseline,
        fleet_instances=fleet_instances,
    )

    store = ArtifactStore(
        args.artifact_root, codeplug.radio_id, codeplug.radio_instance_id
    )
    render_options: dict[str, Any] = {"radio_name": radio.name}
    if fleet_instances is not None:
        render_options["fleet_instances"] = fleet_instances
    reference_path = store.directory / "reference.html"
    write_html_reference(reference_path, codeplug, **render_options)
    write_markdown_reference(store.directory / "reference.md", codeplug, **render_options)
    export_path = store.directory / path.file_name
    export_path.write_text(export, encoding="utf-8")
    store.record("generate", "success", artifacts=(export_path, reference_path))
    return Build(
        radio=radio,
        codeplug=codeplug,
        path=path,
        export_path=export_path,
        reference_path=reference_path,
        store=store,
        fitted=fitted,
    )


def _describe(build: Build) -> None:
    channels = len(build.codeplug.channels)
    zones = len(build.codeplug.zones)
    print(f"{build.radio.name}: {channels} channel(s) in {zones} zone(s)")
    print(f"  codeplug:  {build.export_path}  ({build.path.export_format})")
    print(f"  reference: {build.reference_path}")


def _command_build(args: argparse.Namespace) -> int:
    build = _build(args)
    _describe(build)
    if not build.path.can_program:
        print(f"  write:     unavailable -- {build.path.no_backend_reason}")
    else:
        print(
            f"  write:     `codeplugger program` can program this with "
            f"{build.path.backend}"
        )
    return 0


# -- program --------------------------------------------------------------


def _require_port(args: argparse.Namespace, backend: str) -> str:
    if not args.port:
        raise CommandError(
            f"the {backend} backend needs the radio's serial port; pass --port "
            "(e.g. --port /dev/cu.usbserial-10)"
        )
    return str(args.port)


def _benlink_address(args: argparse.Namespace, target: Any, writer: Any) -> str:
    """Use the given Bluetooth address, or find exactly one matching radio.

    Discovery exists because a macOS address is a per-host CoreBluetooth UUID
    that is printed nowhere on the radio, so asking an operator to supply one
    is only reasonable after they have seen it once.
    """

    if args.address:
        return str(args.address)
    found = writer.discover(target)
    if not found:
        raise CommandError(
            "no matching Bluetooth radio found; switch the radio on, pair it, "
            "then pass --address"
        )
    if len(found) > 1:
        listing = "\n".join(f"  {address}  {name}" for address, name in found)
        raise CommandError(f"several radios match; choose one with --address:\n{listing}")
    address, name = found[0]
    print(f"Found {name} at {address}")
    return address


def _program(build: Build, args: argparse.Namespace) -> None:
    stamp = _timestamp()
    directory = build.store.directory
    backend = build.path.backend
    capabilities = build.radio.capabilities

    if backend == "chirp-writer":
        from .backends.chirp import ChirpTarget, ChirpWriter

        target = ChirpTarget.from_capabilities(capabilities)
        if target.needs_operator:
            print(
                "This radio must be put into clone mode by hand for each "
                "transfer; follow the prompts.",
                file=sys.stderr,
            )
        ChirpWriter(
            executable=args.chirp_writer, artifact_store=build.store
        ).write(
            build.export_path,
            _require_port(args, backend),
            target=target,
            backup=directory / f"backup-{stamp}.img",
            read_back=directory / f"read-back-{stamp}.img",
            confirm=True,
        )
        return

    if backend == "dmrconf":
        from .backends.dmrconf import Dmrconf

        Dmrconf(executable=args.dmrconf, artifact_store=build.store).write(
            build.export_path,
            radio_key=str(build.path.radio_key),
            backup=directory / f"backup-{stamp}.bin",
            read_back=directory / f"read-back-{stamp}.bin",
            confirm=True,
        )
        return

    if backend == "p64tool":
        from .backends.p64tool import P64Tool

        P64Tool(executable=args.p64tool, artifact_store=build.store).write(
            _require_port(args, backend),
            backup=directory / f"backup-{stamp}.bin",
            config=build.export_path,
            confirm=True,
        )
        return

    if backend == "benlink":
        from .backends.benlink import BenlinkTarget, BenlinkWriter

        target = BenlinkTarget.from_capabilities(capabilities)
        writer = BenlinkWriter(artifact_store=build.store)
        writer.write(
            json.loads(build.export_path.read_text(encoding="utf-8")),
            _benlink_address(args, target, writer),
            target=target,
            backup=directory / f"backup-{stamp}.json",
            read_back=directory / f"read-back-{stamp}.json",
            confirm=True,
            allow_unverified=args.allow_unverified,
        )
        return

    raise CommandError(f"no programming backend named '{backend}'")


def _command_program(args: argparse.Namespace) -> int:
    build = _build(args)
    _describe(build)

    if not build.path.can_program:
        raise CommandError(str(build.path.no_backend_reason))
    if not args.confirm:
        print(
            f"\nNothing was written. Re-run with --confirm to program "
            f"{build.radio.name} over {build.path.backend}."
        )
        return 0

    print(f"\nProgramming {build.radio.name} with {build.path.backend}...")
    try:
        _program(build, args)
    except Exception as exc:  # each backend raises its own error type
        raise CommandError(str(exc)) from exc
    print(f"Done. Backup and read-back are in {build.store.directory}")
    return 0


# -- argument parsing -----------------------------------------------------


def _zone_list(value: str) -> list[str]:
    zones = [part.strip() for part in value.split(",") if part.strip()]
    if not zones:
        raise argparse.ArgumentTypeError("--zones needs at least one zone ID")
    return zones


def _add_build_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_argument_group("what to build")
    source.add_argument("--radio", help="radio ID, as listed by `codeplugger radios`")
    source.add_argument(
        "--use-case", help="use-case ID, as listed by `codeplugger use-cases`"
    )
    source.add_argument(
        "--profile",
        default=None,
        help=(
            "a profile file, a directory, a git URL (optionally with #ref), or "
            "the name of a registered collection as listed by `codeplugger "
            "profiles` (then --radio picks the profile in it)"
        ),
    )
    source.add_argument(
        "--zones",
        type=_zone_list,
        default=None,
        help="comma-separated zone IDs to keep, instead of letting the fit choose",
    )
    source.add_argument(
        "--accept-fit",
        action="store_true",
        help="accept a trimmed use case without being asked (for scripts)",
    )
    source.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="decoded baseline for radios exported by editing one (P4)",
    )

    where = parser.add_argument_group("inputs and outputs")
    where.add_argument(
        "--ssrf-root",
        type=Path,
        action="append",
        default=None,
        help=(
            "SSRF root in precedence order; repeat for overlays "
            "(default: the data shipped in the installed ssrf-lite package)"
        ),
    )
    where.add_argument("--radio-root", type=Path, default=DEFAULT_RADIO_ROOT)
    where.add_argument("--use-case-root", type=Path, default=DEFAULT_USE_CASE_ROOT)
    where.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    where.add_argument(
        "--checkout-root",
        type=Path,
        default=DEFAULT_CHECKOUT_ROOT,
        help=(
            "directory holding the sibling checkouts a profile collection names "
            f"(default: {DEFAULT_CHECKOUT_ROOT})"
        ),
    )
    where.add_argument(
        "--cache-root",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help=f"where --profile clones git URLs (default: {DEFAULT_CACHE_ROOT})",
    )
    where.add_argument(
        "--refresh",
        action="store_true",
        help="update an already-cloned profile repository before building",
    )
    where.add_argument(
        "--instance-registry",
        type=Path,
        default=None,
        help="optional instance registry, for fleet radios with their own IDs",
    )
    where.add_argument(
        "--instance",
        default=None,
        help=(
            "registry instance to build for; lets a shared profile that names "
            "no radio_instance take that radio's IDs (requires --instance-registry)"
        ),
    )
    where.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("."),
        help="write .artifacts/<radio>/<profile> below this directory (default: .)",
    )


def _check_build_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.instance is not None and args.instance_registry is None:
        parser.error("--instance requires --instance-registry")
    if args.profile is not None:
        if args.use_case or args.zones:
            parser.error("--profile cannot be combined with --use-case/--zones")
        return
    missing = [
        flag for flag, value in (("--radio", args.radio), ("--use-case", args.use_case))
        if not value
    ]
    if missing:
        parser.error(f"{' and '.join(missing)} required (or use --profile)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codeplugger",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    radios = subparsers.add_parser("radios", help="list the radios that are supported")
    radios.add_argument("--radio-root", type=Path, default=DEFAULT_RADIO_ROOT)
    radios.add_argument("--json", action="store_true")
    radios.set_defaults(handler=_command_radios)

    use_cases = subparsers.add_parser(
        "use-cases", help="list the ready-made channel sets"
    )
    use_cases.add_argument("--use-case-root", type=Path, default=DEFAULT_USE_CASE_ROOT)
    use_cases.add_argument("--json", action="store_true")
    use_cases.set_defaults(handler=_command_use_cases)

    profiles = subparsers.add_parser(
        "profiles", help="list the published community profile collections"
    )
    profiles.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH)
    profiles.add_argument("--checkout-root", type=Path, default=DEFAULT_CHECKOUT_ROOT)
    profiles.add_argument("--json", action="store_true")
    profiles.set_defaults(handler=_command_profiles)

    build = subparsers.add_parser(
        "build",
        help="compile a codeplug and its reference, without touching a radio",
    )
    _add_build_arguments(build)
    build.set_defaults(handler=_command_build)

    program = subparsers.add_parser(
        "program", help="compile a codeplug and write it to the radio"
    )
    _add_build_arguments(program)
    device = program.add_argument_group("the radio")
    device.add_argument("--port", default=None, help="serial port of the programming cable")
    device.add_argument(
        "--address", default=None, help="Bluetooth address, for Benshi radios"
    )
    device.add_argument(
        "--confirm",
        action="store_true",
        help="actually write; without it the build runs and the radio is untouched",
    )
    device.add_argument(
        "--allow-unverified",
        action="store_true",
        help="program a Bluetooth radio model nobody has verified a write on",
    )
    tools = program.add_argument_group("external tools")
    tools.add_argument("--chirp-writer", default="chirp-writer")
    tools.add_argument("--dmrconf", default="dmrconf")
    tools.add_argument("--p64tool", default="p64tool")
    program.set_defaults(handler=_command_program)

    args = parser.parse_args(argv)
    if args.command in {"build", "program"}:
        _check_build_arguments(parser, args)
    try:
        return int(args.handler(args))
    except (CommandError, ProfileValidationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
