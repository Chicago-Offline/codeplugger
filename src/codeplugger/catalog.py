"""What a new operator can choose between: radios, use cases, shared profiles.

`codeplugger-profile` answers "is this profile valid for this radio", which
assumes a profile already exists. This module backs the question that comes
first -- "I have this radio in a box, what can I put on it?" -- by listing the
radios in `radios/`, the abstract use-case profiles in `examples/usecases/`,
and the community profile collections in `site/codeplug-sources.json`, and by
deciding which exporter and programming backend a radio is addressed through.

The lists are derived, never curated: a radio appears because it has a
`capabilities.json`, a use case because it is a profile that names no radio,
and a profile source because the site publishes downloads built from it. None
can drift from what the compiler accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .profile import (
    DEFAULT_RADIO_ROOT,
    PROJECT_ROOT,
    ProfileValidationError,
    _load_capabilities,
    _load_mapping,
    _load_profile_with_inheritance,
    installed_ssrf_root,
)

DEFAULT_USE_CASE_ROOT = PROJECT_ROOT / "examples" / "usecases"
DEFAULT_SOURCES_PATH = PROJECT_ROOT / "site" / "codeplug-sources.json"
# Sibling directories, the layout `tools/build_site_artifacts.py` and CI both
# assume: a source names a checkout, not a path, so one machine's ~/src does
# not end up baked into shared data.
DEFAULT_CHECKOUT_ROOT = PROJECT_ROOT.parent
DEFAULT_CACHE_ROOT = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "codeplugger"
    / "sources"
)


@dataclass(frozen=True)
class Radio:
    """One radio codeplugger can compile for."""

    id: str
    name: str
    capabilities: dict[str, Any]

    @property
    def modes(self) -> tuple[str, ...]:
        return tuple(str(mode) for mode in self.capabilities.get("modes", ()))

    @property
    def max_channels(self) -> int:
        return int(self.capabilities["limits"]["max_channels"])

    @property
    def max_zones(self) -> int:
        return int(self.capabilities["limits"]["max_zones"])


@dataclass(frozen=True)
class UseCase:
    """An abstract base profile: a zone layout with no radio bound to it."""

    id: str
    name: str
    path: Path
    profile: dict[str, Any]

    @property
    def zones(self) -> tuple[tuple[str, str], ...]:
        """``(id, name)`` for each zone, in the order the base lists them."""

        return tuple(
            (str(zone["id"]), str(zone["name"])) for zone in self.profile["zones"]
        )


@dataclass(frozen=True)
class ProgrammingPath:
    """How one radio is exported and, where possible, programmed.

    The export format and the backend are two different answers: a radio can
    have a perfectly good export and no way to write it (the BF-C50 has no
    CHIRP driver), which is why ``backend`` is optional and carries the reason
    it is absent.
    """

    export_format: str
    file_name: str
    backend: str | None = None
    radio_key: str | None = None
    baseline_required: bool = False
    no_backend_reason: str | None = None

    @property
    def can_program(self) -> bool:
        return self.backend is not None


_EXPORT_FILE_NAMES = {
    "chirp-csv": "codeplug.csv",
    "qdmr-yaml": "codeplug.yaml",
    "p64-toml": "codeplug.toml",
    "benlink-plan": "codeplug.json",
}


def programming_path(capabilities: Mapping[str, Any]) -> ProgrammingPath:
    """Pick the exporter and backend a radio's capabilities declare.

    Each backend is identified by its own block, so adding a radio is still a
    capabilities file and nothing else. The order below is "most specific
    first": a Benshi radio and a P4 are both DMR-capable, but neither is
    reachable through the generic paths.
    """

    if "p64tool" in capabilities:
        return ProgrammingPath(
            export_format="p64-toml",
            file_name=_EXPORT_FILE_NAMES["p64-toml"],
            backend="p64tool",
            baseline_required=True,
        )
    if "benlink" in capabilities:
        return ProgrammingPath(
            export_format="benlink-plan",
            file_name=_EXPORT_FILE_NAMES["benlink-plan"],
            backend="benlink",
        )
    if "dmrconf" in capabilities:
        return ProgrammingPath(
            export_format="qdmr-yaml",
            file_name=_EXPORT_FILE_NAMES["qdmr-yaml"],
            backend="dmrconf",
            radio_key=str(capabilities["dmrconf"]["radio_key"]),
        )
    if "chirp" in capabilities:
        return ProgrammingPath(
            export_format="chirp-csv",
            file_name=_EXPORT_FILE_NAMES["chirp-csv"],
            backend="chirp-writer",
        )

    # No backend block: still exportable, because the format follows from what
    # the radio does rather than from how it is written to.
    digital = "DMR" in {str(mode).upper() for mode in capabilities.get("modes", ())}
    export_format = "qdmr-yaml" if digital else "chirp-csv"
    return ProgrammingPath(
        export_format=export_format,
        file_name=_EXPORT_FILE_NAMES[export_format],
        no_backend_reason=(
            f"'{capabilities.get('id', '?')}' declares no programming backend; "
            "export the file and load it with a CPS tool"
        ),
    )


def available_radios(radio_root: Path = DEFAULT_RADIO_ROOT) -> list[Radio]:
    """Return every radio with a capabilities file, sorted by id."""

    radios: list[Radio] = []
    for directory in sorted(path for path in radio_root.iterdir() if path.is_dir()):
        if not (directory / "capabilities.json").is_file():
            continue
        capabilities = _load_capabilities(directory.name, radio_root)
        radios.append(
            Radio(
                id=str(capabilities["id"]),
                name=str(capabilities["name"]),
                capabilities=capabilities,
            )
        )
    return radios


def find_radio(radio_id: str, radio_root: Path = DEFAULT_RADIO_ROOT) -> Radio:
    """Load one radio by id, listing the alternatives when it is unknown."""

    try:
        capabilities = _load_capabilities(radio_id, radio_root)
    except (OSError, ProfileValidationError, ValueError) as exc:
        known = ", ".join(radio.id for radio in available_radios(radio_root))
        raise ProfileValidationError(
            f"unknown radio '{radio_id}': {exc}\nsupported radios: {known}"
        ) from exc
    return Radio(
        id=str(capabilities["id"]),
        name=str(capabilities["name"]),
        capabilities=capabilities,
    )


def available_use_cases(root: Path = DEFAULT_USE_CASE_ROOT) -> list[UseCase]:
    """Return every use-case base profile in ``root``, sorted by id."""

    use_cases: list[UseCase] = []
    for path in sorted(root.glob("*.yml")):
        profile = _load_profile_with_inheritance(path)
        if "radio" in profile:
            # A profile bound to a radio is somebody's codeplug, not a use case.
            continue
        use_cases.append(
            UseCase(
                id=path.stem,
                name=str(profile.get("name", path.stem)),
                path=path,
                profile=profile,
            )
        )
    return use_cases


def find_use_case(use_case_id: str, root: Path = DEFAULT_USE_CASE_ROOT) -> UseCase:
    """Load one use case by id, listing the alternatives when it is unknown."""

    for use_case in available_use_cases(root):
        if use_case.id == use_case_id:
            return use_case
    known = ", ".join(use_case.id for use_case in available_use_cases(root))
    raise ProfileValidationError(
        f"unknown use case '{use_case_id}'; available use cases: {known}"
    )


@dataclass(frozen=True)
class ProfileSource:
    """A collection of ready-made profiles somebody maintains and publishes.

    The same entries the site builds its downloads from, so a profile anyone
    can download is a profile anyone can program by name. ``checkout`` is a
    sibling directory name rather than a path; a source without one ships in
    this repository.

    A source made from a git URL instead of the registry carries ``local_root``
    and an empty ``profiles`` list: nobody has reviewed which files in it are
    profiles, so they are discovered by scanning rather than trusted from an
    index.
    """

    id: str
    name: str
    repo: str | None
    checkout: str | None
    profiles: tuple[str, ...]
    ssrf_roots: tuple[Mapping[str, Any], ...]
    description: str = ""
    local_root: Path | None = field(default=None, compare=False)

    @property
    def is_indexed(self) -> bool:
        """True when the registry says which files in it are profiles."""

        return bool(self.profiles)

    def root(self, checkout_root: Path = DEFAULT_CHECKOUT_ROOT) -> Path:
        """Where this source's files live, or where they would have to be."""

        if self.local_root is not None:
            return self.local_root
        if self.checkout is None:
            return PROJECT_ROOT
        return checkout_root / self.checkout

    def is_available(self, checkout_root: Path = DEFAULT_CHECKOUT_ROOT) -> bool:
        return self.root(checkout_root).is_dir()


@dataclass(frozen=True)
class SharedProfile:
    """One profile inside a source, with the SSRF roots it must resolve against."""

    source: ProfileSource
    path: Path
    id: str
    name: str
    radio: str
    ssrf_roots: tuple[Path, ...]


def available_profile_sources(
    sources_path: Path = DEFAULT_SOURCES_PATH,
) -> list[ProfileSource]:
    """Return the published profile collections, in file order."""

    data = json.loads(sources_path.read_text(encoding="utf-8"))
    return [
        ProfileSource(
            id=str(entry["id"]),
            name=str(entry.get("name", entry["id"])),
            description=str(entry.get("description", "")),
            repo=entry.get("repo"),
            checkout=entry.get("checkout"),
            profiles=tuple(str(item) for item in entry.get("profiles", ())),
            ssrf_roots=tuple(entry.get("ssrf_roots", ())),
        )
        for entry in data.get("sources", [])
    ]


def find_profile_source(
    name: str, sources_path: Path = DEFAULT_SOURCES_PATH
) -> ProfileSource:
    """Resolve a source by id, accepting any unambiguous abbreviation of it."""

    sources = available_profile_sources(sources_path)
    for source in sources:
        if source.id == name:
            return source
    matches = [source for source in sources if name.lower() in source.id.lower()]
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(source.id for source in sources)
    if matches:
        raise ProfileValidationError(
            f"'{name}' matches several profile collections "
            f"({', '.join(source.id for source in matches)}); name one exactly"
        )
    raise ProfileValidationError(
        f"unknown profile collection '{name}'; available collections: {known}"
    )


def _source_ssrf_roots(
    source: ProfileSource, checkout_root: Path
) -> tuple[Path, ...]:
    roots: list[Path] = []
    for entry in source.ssrf_roots:
        checkout = entry.get("checkout")
        if checkout is None:
            roots.append(PROJECT_ROOT / str(entry.get("path", "")))
            continue
        base = checkout_root / str(checkout)
        if not base.is_dir():
            if str(checkout) == "ssrf-lite":
                # The installed package is pinned to a revision and carries the
                # same data, so this checkout is a convenience, not a dependency.
                roots.append(installed_ssrf_root())
                continue
            raise ProfileValidationError(
                f"profile collection '{source.id}' needs a checkout of "
                f"'{checkout}' at {base}; clone it there or pass --checkout-root"
            )
        roots.append(base / str(entry.get("path", "")))
    if roots:
        return tuple(roots)

    # An unindexed repository declares no roots, so fall back to the national
    # data plus the repository's own `ssrf/` overlay if it ships one -- the
    # layout every Chicago Offline overlay already uses.
    roots.append(installed_ssrf_root())
    overlay = source.root(checkout_root) / "ssrf"
    if overlay.is_dir():
        roots.append(overlay)
    return tuple(roots)


# Schemes git itself will run a helper program for. `ext::` executes an
# arbitrary command, which is not something a profile URL gets to do.
_SAFE_URL = re.compile(
    r"^(https?|ssh|git|file)://[^\s]+$|^[\w.+-]+@[\w.-]+:[^\s]+$"
)


def looks_like_git_url(value: str) -> bool:
    """True when ``--profile`` was given a repository to clone."""

    candidate = value.split("#", 1)[0]
    return bool(_SAFE_URL.match(candidate)) or candidate.endswith(".git")


def _cache_directory(url: str, cache_root: Path) -> Path:
    """A stable directory per URL, collision-proof without being cryptic."""

    slug = re.sub(r"[^A-Za-z0-9._-]", "-", url.rstrip("/").split("/")[-1])
    slug = slug.removesuffix(".git") or "profiles"
    return cache_root / f"{slug}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


def _git(args: list[str], *, where: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise ProfileValidationError(f"{where}: could not run git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProfileValidationError(f"{where}: git failed: {detail}")
    return result.stdout.strip()


def clone_profile_repo(
    url: str,
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    refresh: bool = False,
) -> ProfileSource:
    """Clone (or reuse) a profile repository and describe it as a source.

    ``url`` may carry a ``#ref`` suffix to pin a branch or tag. The clone is
    shallow and is reused on later runs: a profile repository is somebody
    else's data, and silently re-fetching it would mean the codeplug built
    today is not the one built yesterday. ``refresh`` asks for the update
    explicitly.
    """

    url, _, ref = url.partition("#")
    if url.startswith("-") or not looks_like_git_url(url):
        raise ProfileValidationError(
            f"'{url}' is not a git URL codeplugger will clone; use https://, "
            "ssh://, git@host:path or file:// (git's ext:: transport runs "
            "arbitrary commands and is refused)"
        )

    directory = _cache_directory(url if not ref else f"{url}#{ref}", cache_root)
    if directory.is_dir():
        if refresh:
            _git(["-C", str(directory), "fetch", "--depth", "1", "origin", ref or "HEAD"],
                 where=url)
            _git(["-C", str(directory), "reset", "--hard", "FETCH_HEAD"], where=url)
    else:
        cache_root.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--quiet"]
        if ref:
            args += ["--branch", ref]
        _git([*args, "--", url, str(directory)], where=url)

    name = directory.name.rsplit("-", 1)[0]
    return ProfileSource(
        id=name,
        name=f"{name} ({url}{'#' + ref if ref else ''})",
        repo=url,
        checkout=None,
        profiles=(),
        ssrf_roots=(),
        description="cloned on demand; not in the profile registry",
        local_root=directory,
    )


def repo_revision(directory: Path) -> str | None:
    """Short revision of ``directory``, when it is a git checkout.

    Worth printing for an unindexed source: it is the only record of which
    version of somebody else's data a codeplug was built from.
    """

    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def local_profile_repo(directory: Path) -> ProfileSource:
    """Describe a directory of profiles as a source, without cloning."""

    directory = directory.resolve()
    if not directory.is_dir():
        raise ProfileValidationError(f"'{directory}' is not a directory")
    return ProfileSource(
        id=directory.name,
        name=f"{directory.name} ({directory})",
        repo=None,
        checkout=None,
        profiles=(),
        ssrf_roots=(),
        description="a local directory; not in the profile registry",
        local_root=directory,
    )


def _candidate_files(root: Path) -> list[Path]:
    """YAML files worth inspecting, excluding anything git is not tracking.

    A working directory holds build output -- a profiles repository that
    publishes its own site has a copy of every profile under `_site/` -- and
    scanning it would offer the same profile twice under one id.
    """

    if (root / ".git").exists():
        try:
            listing = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "*.yml", "*.yaml"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            listing = None
        if listing is not None and listing.returncode == 0:
            return sorted(
                root / name for name in listing.stdout.split("\0") if name
            )
    return sorted(root.rglob("*.y*ml"))


def _scan_for_profiles(root: Path) -> list[Path]:
    """Find the concrete profiles in a repository nobody has indexed.

    A profile is recognized by what it is -- schema version 0.1 with a radio --
    rather than by where it sits, so a repository does not have to adopt this
    project's directory layout to be usable. Abstract bases are skipped: they
    name no radio and cannot be programmed on their own.
    """

    found: list[Path] = []
    for path in _candidate_files(root):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        try:
            document = _load_mapping(path)
        except (OSError, ProfileValidationError, ValueError):
            continue
        if document.get("version") == "0.1" and document.get("radio"):
            found.append(path)
    return found


def source_profiles(
    source: ProfileSource,
    checkout_root: Path = DEFAULT_CHECKOUT_ROOT,
) -> list[SharedProfile]:
    """Load every profile a source offers, in the order it offers them."""

    root = source.root(checkout_root)
    if not root.is_dir():
        clone = (
            f"git clone {source.repo} {root}"
            if source.repo
            else f"put it at {root}"
        )
        raise ProfileValidationError(
            f"profile collection '{source.id}' is not checked out at {root}; "
            f"{clone}"
        )
    ssrf_roots = _source_ssrf_roots(source, checkout_root)

    if source.is_indexed:
        paths: list[Path] = []
        for relative in source.profiles:
            path = root / relative
            if not path.is_file():
                raise ProfileValidationError(
                    f"profile collection '{source.id}' lists '{relative}', which "
                    f"is missing from {root}; the checkout may be out of date"
                )
            paths.append(path)
    else:
        paths = _scan_for_profiles(root)
        if not paths:
            raise ProfileValidationError(
                f"no codeplugger profiles found in {root}; a profile is a YAML "
                "file with version 0.1 and a 'radio'"
            )

    profiles: list[SharedProfile] = []
    for path in paths:
        document = _load_mapping(path)
        radio = document.get("radio")
        if radio is None:
            # Follow the inheritance chain only when the leaf is abstract.
            radio = _load_profile_with_inheritance(path).get("radio")
        if radio is None:
            continue
        profiles.append(
            SharedProfile(
                source=source,
                path=path,
                id=str(document.get("id", path.stem)),
                name=str(document.get("name", path.stem)),
                radio=str(radio),
                ssrf_roots=ssrf_roots,
            )
        )
    return profiles


def _registered_twin(
    source: ProfileSource, sources_path: Path
) -> tuple[ProfileSource, bool] | None:
    """The registry entry for the same repository, and whether it is certain.

    A repository the registry already covers should behave the way the registry
    says: the profiles in `chioff-codeplugger-profiles-shared` select from an
    SSRF overlay in a second repository, and nothing inside the cloned
    repository says so. A matching repo URL is proof; a matching directory name
    is only a hint, so that case borrows the SSRF roots and still discovers the
    profiles by scanning.
    """

    try:
        registered = available_profile_sources(sources_path)
    except (OSError, ValueError):
        return None

    if source.repo:
        wanted = source.repo.removesuffix(".git").rstrip("/")
        for candidate in registered:
            repo = (candidate.repo or "").removesuffix(".git").rstrip("/")
            if repo and repo == wanted:
                return candidate, True

    root = source.local_root
    if root is not None:
        for candidate in registered:
            if candidate.checkout and candidate.checkout == source.id:
                return candidate, False
    return None


def find_shared_profile(
    name: str,
    *,
    radio_id: str | None = None,
    sources_path: Path = DEFAULT_SOURCES_PATH,
    checkout_root: Path = DEFAULT_CHECKOUT_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    refresh: bool = False,
) -> SharedProfile:
    """Resolve a registry name, a git URL or a directory down to one profile.

    A collection carries one profile per radio in the usual case, so ``--radio``
    is the whole selector. Where it does not, the selector narrows by profile
    id or path rather than picking one, because choosing between two codeplugs
    somebody wrote on purpose is not a decision this tool should make.
    """

    if looks_like_git_url(name):
        source = clone_profile_repo(name, cache_root=cache_root, refresh=refresh)
        selector = ""
    elif Path(name).is_dir():
        source = local_profile_repo(Path(name))
        selector = ""
    else:
        collection, _, selector = name.partition(":")
        source = find_profile_source(collection, sources_path)

    if not source.is_indexed:
        twin = _registered_twin(source, sources_path)
        if twin is not None:
            registered, certain = twin
            source = replace(
                source,
                ssrf_roots=registered.ssrf_roots,
                profiles=registered.profiles if certain else (),
            )

    candidates = source_profiles(source, checkout_root)
    if radio_id is not None:
        for_radio = [profile for profile in candidates if profile.radio == radio_id]
        if not for_radio:
            offered = ", ".join(sorted({profile.radio for profile in candidates}))
            raise ProfileValidationError(
                f"'{source.id}' has no profile for radio '{radio_id}'; it covers: "
                f"{offered}"
            )
        candidates = for_radio
    if selector:
        candidates = [
            profile
            for profile in candidates
            if selector in profile.id or selector in str(profile.path)
        ]
        if not candidates:
            raise ProfileValidationError(
                f"'{source.id}' has no profile matching '{selector}'"
            )
    if len(candidates) > 1:
        listing = "\n".join(
            f"  {profile.id}  {profile.path}" for profile in candidates
        )
        raise ProfileValidationError(
            f"'{source.id}' offers several profiles here; choose one with "
            f"--profile {source.id}:<id>\n{listing}"
        )
    if not candidates:
        raise ProfileValidationError(f"'{source.id}' lists no usable profiles")
    return candidates[0]


def looks_like_path(value: str) -> bool:
    """True when ``--profile`` was given a file rather than a collection name."""

    if looks_like_git_url(value):
        return False
    return (
        value.endswith((".yml", ".yaml"))
        or "/" in value
        or os.sep in value
        or Path(value).exists()
    )
