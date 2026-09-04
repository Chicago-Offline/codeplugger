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