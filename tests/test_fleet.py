from __future__ import annotations

from pathlib import Path

import yaml

from codeplugger.fleet import plan_registry
from test_profile import _write_instance_registry, _write_profile, _write_radio, _write_ssrf


def test_plan_registry_resolves_profiles_by_registry_instance(tmp_path: Path) -> None:
    profile = tmp_path / "source.yml"
    _write_profile(profile, ["asg_one"])
    profiles_root = tmp_path / "profiles" / "test_radio"
    profiles_root.mkdir(parents=True)
    profile.rename(profiles_root / "green.yml")
    _write_radio(tmp_path / "radios")
    _write_ssrf(tmp_path / "ssrf")
    registry = _write_instance_registry(
        tmp_path,
        {"dm32_green_01": {"radio": "test_radio", "profile": "test_profile"}},
    )

    plan = plan_registry(
        registry,
        profiles_root.parent,
        [tmp_path / "ssrf"],
        radio_root=tmp_path / "radios",
    )

    assert plan.is_valid
    assert plan.radios[0].id == "dm32_green_01"
    assert plan.radios[0].status == "ready"
    assert plan.radios[0].channel_count == 1
    assert plan.radios[0].resolved_sha256


def test_plan_registry_joins_shared_profile_to_every_radio_of_its_model(
    tmp_path: Path,
) -> None:
    """A profile with no radio_instance serves each registered radio of its model."""

    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    shared = profiles_root / "shared.yml"
    _write_profile(shared, ["asg_one"])
    profile_data = yaml.safe_load(shared.read_text(encoding="utf-8"))
    del profile_data["radio_instance"]
    shared.write_text(yaml.safe_dump(profile_data, sort_keys=False), encoding="utf-8")
    _write_radio(tmp_path / "radios")
    _write_ssrf(tmp_path / "ssrf")
    registry = _write_instance_registry(
        tmp_path,
        {
            "dm32_green_01": {"radio": "test_radio", "profile": "test_profile"},
            "dm32_blue_02": {"radio": "test_radio", "profile": "test_profile"},
        },
    )

    plan = plan_registry(
        registry,
        profiles_root,
        [tmp_path / "ssrf"],
        radio_root=tmp_path / "radios",
    )

    assert plan.is_valid, [radio.error for radio in plan.radios]
    assert [radio.id for radio in plan.radios] == ["dm32_green_01", "dm32_blue_02"]
    assert all(radio.profile == "test_profile" for radio in plan.radios)


def test_plan_registry_rejects_profile_bound_to_another_instance(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    _write_profile(profiles_root / "green.yml", ["asg_one"])
    _write_radio(tmp_path / "radios")
    _write_ssrf(tmp_path / "ssrf")
    registry = _write_instance_registry(
        tmp_path,
        {
            "dm32_green_01": {"radio": "test_radio"},
            "dm32_blue_02": {"radio": "test_radio", "profile": "test_profile"},
        },
    )

    plan = plan_registry(
        registry,
        profiles_root,
        [tmp_path / "ssrf"],
        radio_root=tmp_path / "radios",
    )

    by_id = {radio.id: radio for radio in plan.radios}
    assert by_id["dm32_blue_02"].status == "error"
    assert "bound to radio_instance 'dm32_green_01'" in (by_id["dm32_blue_02"].error or "")


def test_plan_registry_reports_missing_profile(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    registry = _write_instance_registry(
        tmp_path,
        {"dm32_green_01": {"radio": "test_radio", "profile": "missing"}},
    )

    plan = plan_registry(
        registry,
        profiles_root,
        [],
        radio_root=tmp_path / "radios",
    )

    assert not plan.is_valid
    assert "was not found" in (plan.radios[0].error or "")