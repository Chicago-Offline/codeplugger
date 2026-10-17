"""The catalog, the fit, and the `codeplugger` CLI those two back.

These tests run against the repository's real radios, real use cases and the
installed `ssrf-lite` data, because the whole point of the command is that an
operator does not supply any of them. The fits asserted here are the ones the
committed example profiles reach by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeplugger.catalog import (
    available_profile_sources,
    available_radios,
    available_use_cases,
    clone_profile_repo,
    find_profile_source,
    find_radio,
    find_shared_profile,
    find_use_case,
    looks_like_git_url,
    looks_like_path,
    programming_path,
    source_profiles,
)
from codeplugger.cli import main
from codeplugger.fitting import fit_use_case
from codeplugger.profile import ProfileValidationError, default_ssrf_roots


@pytest.fixture(scope="module")
def documents():
    from ssrf import resolve_ssrf_roots

    return resolve_ssrf_roots(default_ssrf_roots(None))


def test_every_radio_in_the_tree_is_offered() -> None:
    ids = {radio.id for radio in available_radios()}
    assert {"baofeng_bf888s", "baofeng_dm32", "btech_uv_pro"} <= ids
    assert all(radio.name for radio in available_radios())


def test_use_cases_are_the_abstract_bases_only() -> None:
    use_cases = {use_case.id for use_case in available_use_cases()}
    assert {"gmrs", "murs", "marine", "pmr446", "multiservice"} == use_cases


def test_each_backend_is_chosen_by_its_own_capabilities_block() -> None:
    assert programming_path(find_radio("baofeng_bf888s").capabilities).backend == (
        "chirp-writer"
    )
    assert programming_path(find_radio("btech_uv_pro").capabilities).backend == "benlink"

    dm32 = programming_path(find_radio("baofeng_dm32").capabilities)
    assert (dm32.backend, dm32.radio_key, dm32.export_format) == (
        "dmrconf",
        "dm32uv",
        "qdmr-yaml",
    )

    p4 = programming_path(find_radio("retevis_matetalk_p4").capabilities)
    assert (p4.backend, p4.baseline_required) == ("p64tool", True)


def test_a_radio_with_no_backend_still_exports() -> None:
    # The BF-C50 omits its chirp block on purpose: no driver exists, and the
    # Retevis RB618 workaround transfers frequencies without tones.
    path = programming_path(find_radio("baofeng_bf_c50").capabilities)
    assert path.export_format == "chirp-csv"
    assert path.can_program is False
    assert "no programming backend" in str(path.no_backend_reason)


def test_unknown_radio_names_the_alternatives() -> None:
    with pytest.raises(ProfileValidationError, match="supported radios:"):
        find_radio("baofeng_uv5r_maxi")


def test_sixteen_knob_positions_keep_the_transmit_channels(documents) -> None:
    fitted = fit_use_case(find_use_case("gmrs"), find_radio("baofeng_bf888s"), documents)

    # Exactly what examples/profiles/baofeng_bf888s/gmrs.yml selects by hand.
    assert fitted.channel_count == 15
    assert [zone["id"] for zone in fitted.profile["zones"]] == ["gmrs"]
    assert fitted.profile["radio"] == "baofeng_bf888s"
    assert "radio_instance" not in fitted.profile

    dropped = {drop.id: drop.reason for drop in fitted.dropped_zones}
    assert "holds 1 zone(s)" in dropped["gmrs_frs"]
    assert "outside the bands" in dropped["weather"]


def test_a_roomy_radio_keeps_everything(documents) -> None:
    fitted = fit_use_case(
        find_use_case("multiservice"), find_radio("baofeng_dm32"), documents
    )

    assert fitted.drops == ()
    assert fitted.channel_count == 100
    assert len(fitted.profile["zones"]) == 7


def test_an_explicit_zone_selection_is_still_fitted(documents) -> None:
    fitted = fit_use_case(
        find_use_case("multiservice"),
        find_radio("baofeng_bf888s"),
        documents,
        zones=["murs", "gmrs"],
    )

    # MURS is VHF and the radio is not, so asking for it does not produce it.
    assert [zone["id"] for zone in fitted.profile["zones"]] == ["gmrs"]
    assert any(drop.id == "murs" for drop in fitted.dropped_zones)


def test_an_unknown_zone_is_an_error_not_a_silent_skip(documents) -> None:
    with pytest.raises(ProfileValidationError, match="has no zone"):
        fit_use_case(
            find_use_case("gmrs"),
            find_radio("baofeng_bf888s"),
            documents,
            zones=["marine_a"],
        )


def test_nothing_that_fits_is_reported_as_such(documents) -> None:
    with pytest.raises(ProfileValidationError, match="nothing from use case"):
        fit_use_case(find_use_case("murs"), find_radio("baofeng_bf888s"), documents)


def test_build_writes_a_codeplug_and_a_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "build",
            "--radio",
            "baofeng_bf888s",
            "--use-case",
            "gmrs",
            "--accept-fit",
            "--artifact-root",
            str(tmp_path),
        ]
    )
    assert exit_code == 0

    directory = tmp_path / ".artifacts" / "baofeng_bf888s" / "baofeng_bf888s_gmrs"
    csv = (directory / "codeplug.csv").read_text(encoding="utf-8")
    assert csv.splitlines()[0].startswith("Location,Name,Frequency")
    assert len(csv.splitlines()) == 16
    assert (directory / "reference.html").is_file()
    assert "chirp-csv" in capsys.readouterr().out


def test_a_dmr_radio_builds_the_format_its_backend_takes(tmp_path: Path) -> None:
    assert (
        main(
            [
                "build",
                "--radio",
                "baofeng_dm32",
                "--use-case",
                "gmrs",
                "--accept-fit",
                "--artifact-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    directory = tmp_path / ".artifacts" / "baofeng_dm32" / "baofeng_dm32_gmrs"
    assert "channels:" in (directory / "codeplug.yaml").read_text(encoding="utf-8")


def test_a_profile_you_already_own_builds_the_same_way(tmp_path: Path) -> None:
    assert (
        main(
            [
                "build",
                "--profile",
                "examples/profiles/baofeng_dm32/example.yml",
                "--instance-registry",
                "examples/instances.yml",
                "--artifact-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    # Named by the registry instance, not by a synthesized use-case id.
    directory = tmp_path / ".artifacts" / "baofeng_dm32" / "example_dm32_01"
    assert (directory / "codeplug.yaml").is_file()


def test_a_use_case_can_be_built_for_one_registered_radio(tmp_path: Path) -> None:
    # The synthesized profile names no radio_instance; --instance supplies
    # one, so the build takes that radio's registry metadata and its name.
    assert (
        main(
            [
                "build",
                "--radio",
                "baofeng_dm32",
                "--use-case",
                "gmrs",
                "--accept-fit",
                "--instance-registry",
                "examples/instances.yml",
                "--instance",
                "example_dm32_01",
                "--artifact-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    directory = tmp_path / ".artifacts" / "baofeng_dm32" / "example_dm32_01"
    assert (directory / "codeplug.yaml").is_file()


def test_instance_needs_a_registry_to_look_it_up_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["build", "--radio", "baofeng_dm32", "--use-case", "gmrs",
              "--instance", "example_dm32_01"])
    assert "--instance requires --instance-registry" in capsys.readouterr().err


def test_a_trimmed_fit_is_refused_without_a_human(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "build",
            "--radio",
            "baofeng_bf888s",
            "--use-case",
            "gmrs",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert "--accept-fit" in capsys.readouterr().err


def test_program_touches_nothing_without_confirm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a dry run must not reach the helper")

    monkeypatch.setattr("codeplugger.backends.chirp.subprocess.run", explode)

    exit_code = main(
        [
            "program",
            "--radio",
            "baofeng_bf888s",
            "--use-case",
            "gmrs",
            "--accept-fit",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert "Re-run with --confirm" in capsys.readouterr().out


def test_program_writes_through_the_backend_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        result = Path(command[command.index("--result") + 1])
        result.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("codeplugger.backends.chirp.subprocess.run", fake_run)

    exit_code = main(
        [
            "program",
            "--radio",
            "baofeng_bf888s",
            "--use-case",
            "gmrs",
            "--accept-fit",
            "--confirm",
            "--port",
            "/dev/cu.usbserial-10",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    command = calls[0]
    assert command[:4] == ["chirp-writer", "write", "--port", "/dev/cu.usbserial-10"]
    # The model gate, the backup and the read-back are not optional here.
    assert command[command.index("--expect-model") + 1] == "Baofeng BF-888"
    assert "--backup" in command and "--read-back" in command

    log = (
        tmp_path
        / ".artifacts"
        / "baofeng_bf888s"
        / "baofeng_bf888s_gmrs"
        / "operations.jsonl"
    ).read_text(encoding="utf-8")
    assert '"operation": "write"' in log


def test_program_without_a_port_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "program",
            "--radio",
            "baofeng_bf888s",
            "--use-case",
            "gmrs",
            "--accept-fit",
            "--confirm",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert "--port" in capsys.readouterr().err


def test_radios_and_use_cases_list_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["radios", "--json"]) == 0
    radios = json.loads(capsys.readouterr().out)
    assert {"id", "name", "backend", "export_format"} <= set(radios[0])

    assert main(["use-cases", "--json"]) == 0
    use_cases = {entry["id"]: entry for entry in json.loads(capsys.readouterr().out)}
    assert [zone["id"] for zone in use_cases["gmrs"]["zones"]] == [
        "gmrs",
        "gmrs_frs",
        "weather",
    ]


# -- community profile collections ----------------------------------------


def test_collections_are_the_ones_the_site_publishes() -> None:
    ids = {source.id for source in available_profile_sources()}
    assert {"use-cases", "chioff-shared"} <= ids


def test_a_collection_answers_to_an_abbreviation() -> None:
    assert find_profile_source("chioff").id == "chioff-shared"
    with pytest.raises(ProfileValidationError, match="unknown profile collection"):
        find_profile_source("nobody-shares-this")


def test_the_radio_picks_the_profile_inside_a_collection() -> None:
    shared = find_shared_profile("use-cases", radio_id="baofeng_bf888s")
    assert shared.id == "example_bf888s_gmrs"
    assert shared.path.is_file()


def test_two_profiles_for_one_radio_is_a_question_not_a_guess() -> None:
    with pytest.raises(ProfileValidationError, match="several profiles"):
        find_shared_profile("use-cases", radio_id="btech_uv_pro")

    narrowed = find_shared_profile("use-cases:murs", radio_id="btech_uv_pro")
    assert narrowed.id == "example_uv_pro_murs"


def test_a_collection_that_does_not_cover_the_radio_says_what_it_does() -> None:
    with pytest.raises(ProfileValidationError, match="no profile for radio"):
        find_shared_profile("use-cases", radio_id="yaesu_vx6r")


def test_an_absent_checkout_prints_the_clone_command(tmp_path: Path) -> None:
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "somebodys-profiles",
                        "name": "Somebody's profiles",
                        "repo": "https://example.invalid/somebodys-profiles",
                        "checkout": "somebodys-profiles",
                        "profiles": ["profiles/radio.yml"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileValidationError, match="git clone"):
        find_shared_profile(
            "somebodys", sources_path=sources, checkout_root=tmp_path / "checkouts"
        )


def test_a_profile_argument_is_a_name_or_a_file_not_both() -> None:
    assert looks_like_path("examples/profiles/x.yml")
    assert looks_like_path("x.yaml")
    assert not looks_like_path("chioff")
    assert not looks_like_path("use-cases:murs")


def test_building_a_collection_profile_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "build",
            "--radio",
            "baofeng_bf888s",
            "--profile",
            "use-cases",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Using example_bf888s_gmrs" in out
    assert (
        tmp_path
        / ".artifacts"
        / "baofeng_bf888s"
        / "example_bf888s_gmrs_01"
        / "codeplug.csv"
    ).is_file()


def test_a_profile_file_that_is_not_for_the_named_radio_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "build",
            "--radio",
            "baofeng_bf888s",
            "--profile",
            "examples/profiles/baofeng_dm32/example.yml",
            "--instance-registry",
            "examples/instances.yml",
            "--artifact-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert "is a profile for 'baofeng_dm32'" in capsys.readouterr().err


def test_profiles_command_reports_what_is_checked_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["profiles", "--json", "--checkout-root", str(tmp_path)]) == 0
    listing = {entry["id"]: entry for entry in json.loads(capsys.readouterr().out)}

    # This repository ships the use cases, so they are always available; a
    # collection that lives elsewhere is reported as missing, not as an error.
    assert listing["use-cases"]["available"] is True
    assert listing["chioff-shared"]["available"] is False
    assert listing["chioff-shared"]["profiles"] == []


def test_every_listed_profile_in_a_checked_out_collection_loads() -> None:
    for source in available_profile_sources():
        if not source.is_available():
            continue
        for profile in source_profiles(source):
            assert profile.radio
            assert profile.path.is_file()


# -- repositories outside the registry ------------------------------------


def _make_profile_repo(root: Path) -> str:
    """A throwaway git repository holding one profile, served over file://."""

    import subprocess

    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "handheld.yml").write_text(
        "version: '0.1'\n"
        "id: somebodys_bf888s\n"
        "name: Somebody's BF-888S\n"
        "radio: baofeng_bf888s\n"
        "zones:\n"
        "  - id: gmrs\n"
        "    name: GMRS\n"
        "    assignments:\n"
        "      - asgn_gmrs_01\n"
        "      - asgn_gmrs_02\n",
        encoding="utf-8",
    )
    # Build output a scan must ignore, or the same profile is offered twice.
    (root / "_site").mkdir()
    (root / "_site" / "handheld.yml").write_text(
        (root / "profiles" / "handheld.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "--quiet", "-b", "main"],
        ["git", "add", "profiles"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--quiet", "-m", "p"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return f"file://{root}"


def test_a_git_url_is_cloned_and_scanned(tmp_path: Path) -> None:
    url = _make_profile_repo(tmp_path / "somebodys-profiles")

    shared = find_shared_profile(
        url, radio_id="baofeng_bf888s", cache_root=tmp_path / "cache"
    )

    assert shared.id == "somebodys_bf888s"
    assert shared.path.is_file()
    # The untracked copy under _site/ must not turn one profile into two.
    assert len(source_profiles(shared.source)) == 1


def test_a_cloned_repo_is_reused_rather_than_refetched(tmp_path: Path) -> None:
    url = _make_profile_repo(tmp_path / "somebodys-profiles")
    cache = tmp_path / "cache"

    first = clone_profile_repo(url, cache_root=cache)
    marker = first.local_root / "profiles" / "scratch.txt"
    marker.write_text("still here", encoding="utf-8")
    second = clone_profile_repo(url, cache_root=cache)

    assert second.local_root == first.local_root
    assert marker.is_file()


def test_transports_that_run_commands_are_refused(tmp_path: Path) -> None:
    for hostile in ("ext::sh -c 'touch /tmp/pwned'", "--upload-pack=touch", "nope"):
        with pytest.raises(ProfileValidationError, match="not a git URL"):
            clone_profile_repo(hostile, cache_root=tmp_path / "cache")


def test_a_git_url_is_not_mistaken_for_a_path() -> None:
    assert looks_like_git_url("https://example.invalid/profiles.git")
    assert looks_like_git_url("git@example.invalid:me/profiles.git")
    assert looks_like_git_url("https://example.invalid/profiles#v2")
    assert not looks_like_path("https://example.invalid/profiles.git")
    assert not looks_like_git_url("examples/profiles/x.yml")


def test_a_local_directory_of_profiles_works_without_cloning(tmp_path: Path) -> None:
    _make_profile_repo(tmp_path / "somebodys-profiles")

    shared = find_shared_profile(
        str(tmp_path / "somebodys-profiles"), radio_id="baofeng_bf888s"
    )

    assert shared.id == "somebodys_bf888s"


def test_a_directory_with_no_profiles_says_what_one_is(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ProfileValidationError, match="version 0.1 and a 'radio'"):
        find_shared_profile(str(tmp_path / "empty"))


def test_building_from_a_git_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = _make_profile_repo(tmp_path / "somebodys-profiles")

    exit_code = main(
        [
            "build",
            "--radio",
            "baofeng_bf888s",
            "--profile",
            url,
            "--cache-root",
            str(tmp_path / "cache"),
            "--artifact-root",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    assert (
        tmp_path / "out" / ".artifacts" / "baofeng_bf888s" / "somebodys_bf888s"
        / "codeplug.csv"
    ).is_file()
    # The revision is printed: it is the only record of what was built from.
    assert "Using somebodys_bf888s" in capsys.readouterr().out
