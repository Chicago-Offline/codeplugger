from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest
import yaml

from codeplugger.profile import (
    ProfileValidationError,
    _load_and_validate_profile,
    load_and_validate_profile,
)
from codeplugger.resolved import ResolvedTones, resolve_codeplug


def _write_profile(path: Path, assignments: list[str], **extra) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "id": "test_profile",
                "name": "Test profile",
                "radio": "test_radio",
                "radio_instance": "dm32_green_01",
                "zones": [
                    {
                        "id": "reference",
                        "name": "Reference",
                        "assignments": assignments,
                    }
                ],
                **extra,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_radio(
    root: Path,
    max_channels_per_zone: int = 2,
    extra_limits: dict | None = None,
) -> None:
    radio = root / "test_radio"
    radio.mkdir(parents=True)
    (radio / "capabilities.json").write_text(
        json.dumps(
            {
                "id": "test_radio",
                "name": "Test radio",
                "limits": {
                    "max_channels": 2,
                    "max_zones": 1,
                    "max_channels_per_zone": max_channels_per_zone,
                    **(extra_limits or {}),
                },
            }
        ),
        encoding="utf-8",
    )


def _write_ssrf(root: Path, contacts: list[dict] | None = None) -> None:
    systems = root / "systems"
    systems.mkdir(parents=True)
    (systems / "fixture.yml").write_text(
        yaml.safe_dump(
            {
                "ssrf_lite_version": "0.5.3",
                "organizations": [{"id": "org_test", "name": "Test"}],
                "stations": [
                    {"id": "stn_test", "organization_id": "org_test"}
                ],
                "antennas": [{"id": "ant_test", "station_id": "stn_test"}],
                "rf_chains": [
                    {
                        "id": "chain_one",
                        "station_id": "stn_test",
                        "antenna_id": "ant_test",
                        "tx": {"freq_mhz": 146.52, "emission": "16K0F3E"},
                        "rx": {"freq_mhz": 146.52},
                        "mode": {"type": "FM"},
                    },
                    {
                        "id": "chain_two",
                        "station_id": "stn_test",
                        "antenna_id": "ant_test",
                        "tx": {"freq_mhz": 446.0, "emission": "16K0F3E"},
                        "rx": {"freq_mhz": 446.0},
                        "mode": {"type": "FM"},
                    },
                ],
                "assignments": [
                    {
                        "id": "asg_one",
                        "rf_chain_id": "chain_one",
                        "usage": "simplex",
                    },
                    {
                        "id": "asg_two",
                        "rf_chain_id": "chain_two",
                        "usage": "simplex",
                    },
                ],
                **({"contacts": contacts} if contacts is not None else {}),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_instance_registry(root: Path, instances: dict[str, dict]) -> Path:
    path = root / "instances.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "instances": instances,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_profile_resolves_ordered_assignment_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one", "asg_two"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        loaded = load_and_validate_profile(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert loaded["zones"][0]["assignments"] == ["asg_one", "asg_two"]
    assert loaded["radio_instance"] == "dm32_green_01"


def test_profile_inherits_and_filters_zones() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profiles = root / "profiles"
        profiles.mkdir()
        (profiles / "base.yml").write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "base_profile",
                    "name": "Base profile",
                    "radio": "test_radio",
                    "radio_instance": "base_instance",
                    "zones": [
                        {
                            "id": "reference",
                            "name": "Base reference",
                            "assignments": [
                                {"id": "asg_one", "display_name": "Base one"}
                            ],
                        },
                        {
                            "id": "secondary",
                            "name": "Secondary",
                            "assignments": ["asg_two"],
                        },
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        child = profiles / "child.yml"
        child.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "child_profile",
                    "name": "Child profile",
                    "radio_instance": "child_instance",
                    "extends": "base.yml",
                    "zones_only": ["reference"],
                    "zones": [
                        {
                            "id": "reference",
                            "name": "Child reference",
                            "assignments": [
                                {"id": "asg_one", "display_name": "Child one"}
                            ],
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        radio = root / "radios" / "test_radio"
        radio.mkdir(parents=True)
        (radio / "capabilities.json").write_text(
            json.dumps(
                {
                    "id": "test_radio",
                    "name": "Test radio",
                    "limits": {
                        "max_channels": 2,
                        "max_zones": 2,
                        "max_channels_per_zone": 2,
                    },
                }
            ),
            encoding="utf-8",
        )
        _write_ssrf(root / "ssrf")

        loaded = load_and_validate_profile(
            child,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert loaded["id"] == "child_profile"
    assert loaded["name"] == "Child profile"
    assert loaded["radio"] == "test_radio"
    assert loaded["radio_instance"] == "child_instance"
    assert "extends" not in loaded
    assert "zones_only" not in loaded
    assert [zone["id"] for zone in loaded["zones"]] == ["reference"]
    assert loaded["zones"][0]["name"] == "Child reference"
    assert loaded["zones"][0]["assignments"] == [
        {"id": "asg_one", "display_name": "Child one"}
    ]


def test_profile_inheritance_rejects_radio_mismatch_and_cycles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        base = root / "base.yml"
        base.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "base",
                    "name": "Base",
                    "radio": "test_radio",
                    "zones": [
                        {
                            "id": "reference",
                            "name": "Reference",
                            "assignments": ["asg_one"],
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        mismatch = root / "mismatch.yml"
        mismatch.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "mismatch",
                    "name": "Mismatch",
                    "radio": "other_radio",
                        "radio_instance": "mismatch_instance",
                    "extends": "base.yml",
                    "zones": [
                        {
                            "id": "reference",
                            "name": "Reference",
                            "assignments": ["asg_one"],
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProfileValidationError, match="does not match parent"):
            load_and_validate_profile(
                mismatch,
                [root / "ssrf"],
                radio_root=root / "radios",
            )

        first = root / "first.yml"
        second = root / "second.yml"
        first.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "first",
                    "name": "First",
                    "radio": "test_radio",
                        "radio_instance": "first_instance",
                    "extends": "second.yml",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        second.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "second",
                    "name": "Second",
                    "radio": "test_radio",
                        "radio_instance": "second_instance",
                    "extends": "first.yml",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProfileValidationError, match="inheritance cycle"):
            load_and_validate_profile(
                first,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_profile_inheritance_requires_child_instance_and_parent_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        profile = root / "child.yml"
        profile.write_text(
            yaml.safe_dump(
                {
                    "version": "0.1",
                    "id": "child",
                    "name": "Child",
                    "extends": "missing.yml",
                    "zones": [
                        {
                            "id": "reference",
                            "name": "Reference",
                            "assignments": ["asg_one"],
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProfileValidationError, match="radio_instance"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )

        profile_data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        profile_data["radio_instance"] = "child_instance"
        profile.write_text(yaml.safe_dump(profile_data, sort_keys=False), encoding="utf-8")
        with pytest.raises(ProfileValidationError, match="could not load parent"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_profile_supports_profile_local_assignment_display_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, [{"id": "asg_one", "display_name": "CO ONE"}])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert resolved.channels[0].display_name == "CO ONE"


def test_simplex_channel_plan_defaults_tx_to_rx_frequency() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_plan"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["channel_plans"] = [
            {
                "id": "plan_test",
                "name": "Test plan",
                "channels": [{"name": "One", "freq_mhz": 446.025}],
            }
        ]
        data["assignments"].append(
            {
                "id": "asg_plan",
                "channel_plan_id": "plan_test",
                "usage": "simplex",
            }
        )
        fixture.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert resolved.channels[0].tx_frequency_mhz == 446.025
    assert resolved.channels[0].tx_permitted


def test_resolved_codeplug_defaults_instance_to_profile_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        profile_data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        del profile_data["radio_instance"]
        profile.write_text(
            yaml.safe_dump(profile_data, sort_keys=False),
            encoding="utf-8",
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert resolved.radio_instance_id == "test_profile"
    assert resolved.radio_instance is None


def test_profile_rejects_unknown_assignment() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_missing"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError, match="asg_missing"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_profile_enforces_zone_channel_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one", "asg_two"])
        _write_radio(root / "radios", max_channels_per_zone=1)
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError, match="expands to 2 channels"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_profile_counts_channel_plan_expansion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_plan"])
        _write_radio(root / "radios", max_channels_per_zone=1)
        _write_ssrf(root / "ssrf")
        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["channel_plans"] = [
            {
                "id": "plan_test",
                "name": "Test plan",
                "channels": [
                    {"name": "One", "freq_mhz": 151.1},
                    {"name": "Two", "freq_mhz": 151.2},
                ],
            }
        ]
        data["assignments"].append(
            {
                "id": "asg_plan",
                "channel_plan_id": "plan_test",
                "usage": "simplex",
            }
        )
        fixture.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        with pytest.raises(ProfileValidationError, match="expands to 2 channels"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_profile_rejects_assignment_without_rf_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_empty"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["assignments"].append({"id": "asg_empty", "usage": "simplex"})
        fixture.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

        with pytest.raises(ProfileValidationError, match="asg_empty"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_resolved_codeplug_preserves_order_overlays_and_rf_facts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_two", "asg_one"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["rf_chains"][0]["mode"].update(
            {
                "ctcss_tx_hz": 100.0,
                "ctcss_rx_hz": 123.0,
                "color_code": 1,
                "timeslots": [1],
            }
        )
        data["rf_chains"][0]["rx"]["freq_mhz"] = 146.34
        data["rf_chains"][0]["tx"]["freq_mhz"] = 146.94
        data["rf_chains"][1]["tx"].pop("freq_mhz")
        data["assignments"][0].update(
            {"channel_name": "Channel one", "service": "amateur"}
        )
        data["assignments"][1]["channel_name"] = "Channel two"
        fixture.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        overlay = root / "overlay" / "overrides"
        overlay.mkdir(parents=True)
        (overlay / "notes.yml").write_text(
            yaml.safe_dump(
                {
                    "ssrf_lite_version": "0.5.3",
                    "overrides": {
                        "assignments": [
                            {
                                "id": "asg_two",
                                "patch": {"notes": "Overlay note"},
                            }
                        ]
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf", root / "overlay"],
            radio_root=root / "radios",
        )

    assert resolved.radio_id == "test_radio"
    assert resolved.radio_instance_id == "dm32_green_01"
    assert [channel.assignment_id for channel in resolved.channels] == [
        "asg_two",
        "asg_one",
    ]
    assert [channel.display_name for channel in resolved.channels] == [
        "Channel two",
        "Channel one",
    ]
    assert resolved.channels[0].rx_frequency_mhz == 446.0
    assert resolved.channels[0].tx_frequency_mhz is None
    assert resolved.channels[0].tx_permitted is False
    assert resolved.channels[0].notes == "Overlay note"
    assert resolved.channels[1].rx_frequency_mhz == 146.34
    assert resolved.channels[1].tx_frequency_mhz == 146.94
    assert resolved.channels[1].service == "amateur"
    # chain_one carries no explicit bandwidth_khz, so 25 kHz is derived from
    # its 16K0F3E emission designator.
    assert resolved.channels[1].bandwidth_khz == 25.0
    assert resolved.channels[1].power_w is None
    assert resolved.channels[1].tones == ResolvedTones(
        ctcss_tx_hz=100.0,
        ctcss_rx_hz=123.0,
    )
    assert resolved.channels[1].color_code == 1
    assert resolved.channels[1].timeslots == (1,)
    assert resolved.channels[1].timeslot == 1
    assert resolved.zones[0].channel_references == ("asg_two", "asg_one")


def test_resolved_codeplug_output_is_repeatable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one", "asg_two"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        first = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )
        second = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert first.to_json() == second.to_json()
    assert first.to_yaml() == second.to_yaml()
    assert json.loads(first.to_json()) == yaml.safe_load(first.to_yaml())

def _write_radio_with_limits(root: Path, extra_limits: dict) -> None:
    """Write a test radio whose capabilities carry additional limit keys."""

    radio = root / "test_radio"
    radio.mkdir(parents=True, exist_ok=True)
    limits = {
        "max_channels": 2,
        "max_zones": 1,
        "max_channels_per_zone": 2,
    }
    limits.update(extra_limits)
    (radio / "capabilities.json").write_text(
        json.dumps({"id": "test_radio", "name": "Test radio", "limits": limits}),
        encoding="utf-8",
    )


def test_profile_enforces_zone_name_length() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["name"] = "A" * 17
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio_with_limits(root / "radios", {"max_zone_name_chars": 16})
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError, match="17 characters"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_zone_name_at_limit_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["name"] = "A" * 16
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio_with_limits(root / "radios", {"max_zone_name_chars": 16})
        _write_ssrf(root / "ssrf")

        loaded = load_and_validate_profile(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert loaded["zones"][0]["name"] == "A" * 16


def test_absent_name_limit_skips_check() -> None:
    """An incomplete capabilities file must degrade, not fail."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["name"] = "A" * 200
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio_with_limits(root / "radios", {})
        _write_ssrf(root / "ssrf")

        loaded = load_and_validate_profile(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert loaded["zones"][0]["name"] == "A" * 200


def test_resolver_enforces_channel_name_length() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_radio_with_limits(root / "radios", {"max_channel_name_chars": 8})
        _write_ssrf(root / "ssrf")
        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["assignments"][0]["display_name"] = "WAY TOO LONG NAME"
        fixture.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        with pytest.raises(ProfileValidationError, match="channel name"):
            resolve_codeplug(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_dm32_capabilities_declare_name_limits() -> None:
    """The shipped DM-32 document must carry the field-verified name limits."""

    capabilities = json.loads(
        (Path(__file__).resolve().parents[1] / "radios" / "baofeng_dm32" / "capabilities.json").read_text(
            encoding="utf-8"
        )
    )
    limits = capabilities["limits"]
    assert limits["max_channel_name_chars"] == 16
    assert limits["max_zone_name_chars"] == 16
    assert limits["max_scan_list_name_chars"] == 10
    assert limits["max_contact_name_chars"] == 16


def test_fm_only_radio_rejects_dmr_assignment_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_ssrf(root / "ssrf")

        radio = root / "radios" / "test_radio"
        radio.mkdir(parents=True)
        (radio / "capabilities.json").write_text(
            json.dumps(
                {
                    "id": "test_radio",
                    "name": "FM-only test radio",
                    "capabilities_version": "0.2",
                    "limits": {
                        "max_channels": 32,
                        "max_zones": 1,
                        "max_channels_per_zone": 32,
                    },
                    "modes": ["FM"],
                }
            ),
            encoding="utf-8",
        )

        fixture = root / "ssrf" / "systems" / "fixture.yml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        data["rf_chains"][0]["mode"]["type"] = "DMR"
        fixture.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

        with pytest.raises(ProfileValidationError, match="does not support"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_uv5r_mini_fixture_profile_validates_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_ssrf(root / "ssrf")

        fixture_profile = (
            Path(__file__).resolve().parents[1]
            / "profiles"
            / "baofeng_uv5r_mini"
            / "reference.yml"
        )
        loaded = load_and_validate_profile(
            fixture_profile,
            [root / "ssrf"],
        )

    assert loaded["radio"] == "baofeng_uv5r_mini"
    assert loaded["zones"][0]["assignments"] == ["asg_one"]


def test_profile_rejects_missing_instance_registry_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        registry = _write_instance_registry(
            root,
            {
                "other_radio_01": {
                    "radio": "test_radio",
                }
            },
        )

        with pytest.raises(ProfileValidationError, match="missing instance"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_profile_rejects_instance_registry_radio_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        registry = _write_instance_registry(
            root,
            {
                "dm32_green_01": {
                    "radio": "some_other_radio",
                }
            },
        )

        with pytest.raises(ProfileValidationError, match="targets radio"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_resolved_codeplug_includes_instance_registry_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")
        registry = _write_instance_registry(
            root,
            {
                "dm32_green_01": {
                    "radio": "test_radio",
                    "label": "Green test radio",
                    "firmware": "TEST.01",
                }
            },
        )

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
            instance_registry_path=registry,
        )

    assert resolved.radio_instance_id == "dm32_green_01"
    assert resolved.radio_instance == {
        "radio": "test_radio",
        "label": "Green test radio",
        "firmware": "TEST.01",
    }


def _write_radio_with_firmware_limits(root: Path) -> None:
    """A radio whose contact capacity changes across firmware versions.

    Mirrors the DM-32UV ROW line: 048 stores more contacts than 049.
    """

    radio = root / "test_radio"
    radio.mkdir(parents=True, exist_ok=True)
    (radio / "capabilities.json").write_text(
        json.dumps(
            {
                "id": "test_radio",
                "name": "Test radio",
                "capabilities_version": "0.2",
                "limits": {
                    "max_channels": 2,
                    "max_zones": 1,
                    "max_channels_per_zone": 2,
                    "max_contacts": 50000,
                },
                "firmware_limits": {
                    "FW.048": {"max_contacts": 150000},
                    "FW.049": {"max_contacts": 50000},
                    "FW.050": {"max_zones": 1, "max_channels": 1},
                },
            }
        ),
        encoding="utf-8",
    )


def _resolve(firmware: str | None) -> dict:
    """Resolve effective limits for ``firmware`` against the fixture radio."""

    from codeplugger.profile import _resolve_firmware_limits

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_radio_with_firmware_limits(root / "radios")
        capabilities = json.loads(
            (root / "radios" / "test_radio" / "capabilities.json").read_text(
                encoding="utf-8"
            )
        )
    limits, source = _resolve_firmware_limits(capabilities, firmware)
    return {"limits": limits, "source": source}


def test_declared_firmware_selects_its_own_limits() -> None:
    """048 gets the larger contact ceiling; 049 gets the smaller one."""

    assert _resolve("FW.048")["limits"]["max_contacts"] == 150000
    assert _resolve("FW.049")["limits"]["max_contacts"] == 50000
    assert "FW.048" in _resolve("FW.048")["source"]


def test_unknown_firmware_falls_back_to_the_conservative_floor() -> None:
    """An unverified radio must not validate against 048-only capacity.

    This is the safety property: absent firmware information, take the minimum
    across every declared firmware rather than the base value.
    """

    resolved = _resolve(None)
    assert resolved["limits"]["max_contacts"] == 50000
    # FW.050 declares a *smaller* channel ceiling, so the floor must pick it up
    # even though the base limits allow 2.
    assert resolved["limits"]["max_channels"] == 1
    assert "conservative" in resolved["source"]


def test_firmware_absent_from_overrides_is_treated_as_unknown() -> None:
    """A radio running firmware we have not characterized gets the floor."""

    resolved = _resolve("FW.999")
    assert resolved["limits"]["max_contacts"] == 50000
    assert resolved["limits"]["max_channels"] == 1
    assert "FW.999" in resolved["source"]
    assert "conservative" in resolved["source"]


def test_radio_without_firmware_limits_is_unchanged() -> None:
    """Radios that declare no firmware_limits keep today's behavior exactly."""

    from codeplugger.profile import _resolve_firmware_limits

    capabilities = {
        "id": "test_radio",
        "name": "Test radio",
        "limits": {"max_channels": 2, "max_zones": 1, "max_channels_per_zone": 2},
    }
    limits, source = _resolve_firmware_limits(capabilities, None)
    assert limits == capabilities["limits"]
    assert source == "radio limit"


def test_instance_firmware_drives_enforcement_end_to_end() -> None:
    """The registry's firmware field must reach the limit check.

    FW.050 declares max_channels 1, so a two-channel profile fails only when
    the instance's firmware is consulted.
    """

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one", "asg_two"])
        _write_radio_with_firmware_limits(root / "radios")
        _write_ssrf(root / "ssrf")
        registry = _write_instance_registry(
            root,
            {
                "dm32_green_01": {
                    "radio": "test_radio",
                    "firmware": "FW.050",
                }
            },
        )

        with pytest.raises(ProfileValidationError, match="firmware FW.050"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_dm32_declares_firmware_dependent_contact_limits() -> None:
    """The real DM-32 file records the 048/049 contact split.

    Base max_contacts must be the SMALLER value so an unknown-firmware
    validation cannot pass a profile that only fits on 048.
    """

    capabilities = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "radios"
            / "baofeng_dm32"
            / "capabilities.json"
        ).read_text(encoding="utf-8")
    )
    firmware_limits = capabilities["firmware_limits"]

    assert firmware_limits["DM32.01.L01.048"]["max_contacts"] == 150000
    assert firmware_limits["DM32.01.01.049"]["max_contacts"] == 50000
    assert capabilities["limits"]["max_contacts"] == 50000
    assert capabilities["limits"]["max_contacts"] == min(
        override["max_contacts"] for override in firmware_limits.values()
    )


def test_profile_extensions_flow_through_to_resolved_codeplug() -> None:
    """Namespaced extensions pass from profile to IR untouched (qdmr-style)."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["extensions"] = {"dm32": {"boot_screen": "logo"}}
        data["zones"][0]["assignments"] = [
            {"id": "asg_one", "extensions": {"dm32": {"scan_list": "city"}}},
            "asg_two",
        ]
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert resolved.extensions == {"dm32": {"boot_screen": "logo"}}
    assert resolved.channels[0].extensions == {"dm32": {"scan_list": "city"}}
    assert resolved.channels[1].extensions == {}
    round_tripped = json.loads(resolved.to_json())
    assert round_tripped["extensions"] == {"dm32": {"boot_screen": "logo"}}
    assert round_tripped["channels"][0]["extensions"] == {
        "dm32": {"scan_list": "city"}
    }


def test_profile_rejects_invalid_extension_namespace() -> None:
    """Namespace keys must be lowercase identifiers."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["extensions"] = {"DM32": {"boot_screen": "logo"}}
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError, match="DM32"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_validation_reports_all_critical_issues_at_once() -> None:
    """One failed run surfaces every critical issue, qdmr RadioLimits style."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_missing", "asg_absent"])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError) as excinfo:
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )

    message = str(excinfo.value)
    assert "asg_missing" in message
    assert "asg_absent" in message


def test_conservative_firmware_fallback_emits_hint() -> None:
    """Falling back to the conservative floor is reported, not silent."""

    from codeplugger.profile import _load_and_validate_profile
    from codeplugger.validation import Severity

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_one"])
        _write_radio_with_firmware_limits(root / "radios")
        _write_ssrf(root / "ssrf")

        _, _, _, report = _load_and_validate_profile(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    hints = [issue for issue in report.issues if issue.severity is Severity.HINT]
    assert hints
    assert "conservative" in hints[0].message
    assert not report.has_critical


_FIXTURE_CONTACTS = [
    {"id": "tg_local", "name": "Local", "kind": "Group", "number": 9},
    {"id": "tg_state", "name": "Statewide", "kind": "Group", "number": 3117},
    {"id": "ct_ops", "name": "Ops", "kind": "Private", "number": 3117001},
]


def test_rx_groups_and_scan_lists_resolve() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            [
                {
                    "id": "asg_one",
                    "contact": "tg_local",
                    "rx_group": "grp_local",
                    "scan_list": "city",
                },
                "asg_two",
            ],
            rx_groups=[
                {
                    "id": "grp_local",
                    "name": "Local",
                    "contacts": ["tg_local", "tg_state"],
                }
            ],
            scan_lists=[
                {
                    "id": "city",
                    "name": "City",
                    "channels": ["asg_one", "asg_two"],
                }
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=_FIXTURE_CONTACTS)

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert [contact.id for contact in resolved.contacts] == [
        "tg_local",
        "tg_state",
    ]
    assert resolved.contacts[0].number == 9
    assert resolved.contacts[0].kind == "group"
    assert resolved.rx_groups[0].id == "grp_local"
    assert resolved.rx_groups[0].contact_ids == ("tg_local", "tg_state")
    assert resolved.scan_lists[0].channel_references == ("asg_one", "asg_two")
    assert resolved.channels[0].contact_id == "tg_local"
    assert resolved.channels[0].rx_group_id == "grp_local"
    assert resolved.channels[0].scan_list_id == "city"
    assert resolved.channels[1].contact_id is None
    round_tripped = json.loads(resolved.to_json())
    assert round_tripped["rx_groups"][0]["contact_ids"] == [
        "tg_local",
        "tg_state",
    ]
    assert round_tripped["scan_lists"][0]["channel_references"] == [
        "asg_one",
        "asg_two",
    ]


def test_assignment_contact_outside_rx_groups_is_included() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, [{"id": "asg_one", "contact": "ct_ops"}])
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=_FIXTURE_CONTACTS)

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
        )

    assert [contact.id for contact in resolved.contacts] == ["ct_ops"]
    assert resolved.contacts[0].kind == "private"


def test_rx_group_with_unknown_contact_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            ["asg_one"],
            rx_groups=[
                {"id": "grp_bad", "name": "Bad", "contacts": ["tg_missing"]}
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=_FIXTURE_CONTACTS)

        with pytest.raises(
            ProfileValidationError, match="unknown SSRF contact 'tg_missing'"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_rx_group_rejects_private_contacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            ["asg_one"],
            rx_groups=[
                {"id": "grp_bad", "name": "Bad", "contacts": ["ct_ops"]}
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=_FIXTURE_CONTACTS)

        with pytest.raises(
            ProfileValidationError, match="group calls only"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_rx_group_contact_without_number_fails() -> None:
    contacts = [{"id": "tg_nonum", "name": "NoNum", "kind": "Group"}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            ["asg_one"],
            rx_groups=[
                {"id": "grp_bad", "name": "Bad", "contacts": ["tg_nonum"]}
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=contacts)

        with pytest.raises(ProfileValidationError, match="no DMR number"):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_scan_list_referencing_unselected_assignment_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            ["asg_one"],
            scan_lists=[
                {"id": "city", "name": "City", "channels": ["asg_two"]}
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf")

        with pytest.raises(
            ProfileValidationError, match="which no zone selects"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )


def test_scan_list_capability_limits_enforced() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            ["asg_one", "asg_two"],
            scan_lists=[
                {
                    "id": "city",
                    "name": "TOO LONG NAME",
                    "channels": ["asg_one", "asg_two"],
                }
            ],
        )
        _write_radio(
            root / "radios",
            extra_limits={
                "max_channels_per_scan_list": 1,
                "max_scan_list_name_chars": 8,
            },
        )
        _write_ssrf(root / "ssrf")

        with pytest.raises(ProfileValidationError) as excinfo:
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )

    message = str(excinfo.value)
    assert "expands to 2 channels" in message
    assert "TOO LONG NAME" in message


def _write_dmr_ssrf(root: Path) -> None:
    """A single DMR assignment/rf_chain, for dmr_id-per-zone tests."""

    systems = root / "systems"
    systems.mkdir(parents=True)
    (systems / "fixture.yml").write_text(
        yaml.safe_dump(
            {
                "ssrf_lite_version": "0.5.3",
                "organizations": [{"id": "org_test", "name": "Test"}],
                "stations": [{"id": "stn_test", "organization_id": "org_test"}],
                "antennas": [{"id": "ant_test", "station_id": "stn_test"}],
                "rf_chains": [
                    {
                        "id": "chain_dmr",
                        "station_id": "stn_test",
                        "antenna_id": "ant_test",
                        "tx": {"freq_mhz": 446.5, "emission": "7K60FXE"},
                        "rx": {"freq_mhz": 446.5},
                        "mode": {
                            "type": "DMR",
                            "color_code": 1,
                            "timeslots": [1],
                        },
                    }
                ],
                "assignments": [
                    {
                        "id": "asg_dmr",
                        "rf_chain_id": "chain_dmr",
                        "usage": "simplex",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_dmr_registry(root: Path, **instance_extra: object) -> Path:
    return _write_instance_registry(
        root,
        {
            "dm32_green_01": {
                "radio": "test_radio",
                "dmr_ids": [
                    {"key": "ham", "id": 1234567, "name": "CALLSIGN"},
                    {"key": "family", "id": 123, "name": "Family"},
                ],
                **instance_extra,
            }
        },
    )


def test_assignment_dmr_id_overrides_zone_and_instance_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, [{"id": "asg_dmr", "dmr_id": "family"}])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["dmr_id"] = "ham"
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root, default_dmr_id="ham")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
            instance_registry_path=registry,
        )

    assert resolved.channels[0].dmr_id_key == "family"
    assert resolved.channels[0].dmr_id == 123


def test_zone_dmr_id_wins_over_instance_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_dmr"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["dmr_id"] = "family"
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root, default_dmr_id="ham")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
            instance_registry_path=registry,
        )

    assert resolved.channels[0].dmr_id_key == "family"
    assert resolved.channels[0].dmr_id == 123


def test_instance_default_dmr_id_used_absent_overrides() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_dmr"])
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root, default_dmr_id="ham")

        resolved = resolve_codeplug(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
            instance_registry_path=registry,
        )

    assert resolved.channels[0].dmr_id_key == "ham"
    assert resolved.channels[0].dmr_id == 1234567


def test_unknown_assignment_dmr_id_key_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, [{"id": "asg_dmr", "dmr_id": "nonexistent"}])
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root)

        with pytest.raises(
            ProfileValidationError, match="nonexistent.*not a known dmr_ids key"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_unknown_zone_dmr_id_key_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_dmr"])
        data = yaml.safe_load(profile.read_text(encoding="utf-8"))
        data["zones"][0]["dmr_id"] = "nonexistent"
        profile.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root)

        with pytest.raises(
            ProfileValidationError, match="nonexistent.*not a known dmr_ids key"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_unknown_instance_default_dmr_id_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_dmr"])
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root, default_dmr_id="nonexistent")

        with pytest.raises(
            ProfileValidationError, match="nonexistent.*not a known dmr_ids key"
        ):
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
                instance_registry_path=registry,
            )


def test_digital_channel_with_no_bound_identity_warns_not_fails() -> None:
    from codeplugger.validation import Severity

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(profile, ["asg_dmr"])
        _write_radio(root / "radios")
        _write_dmr_ssrf(root / "ssrf")
        registry = _write_dmr_registry(root)  # no default_dmr_id declared

        _loaded, _documents, _instance_metadata, report = _load_and_validate_profile(
            profile,
            [root / "ssrf"],
            radio_root=root / "radios",
            instance_registry_path=registry,
        )

    assert not report.has_critical
    warnings = [
        issue for issue in report.issues if issue.severity is Severity.WARNING
    ]
    assert any("no dmr_id bound" in issue.message for issue in warnings)


def test_assignment_referencing_unknown_rx_group_or_scan_list_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profile = root / "profile.yml"
        _write_profile(
            profile,
            [
                {
                    "id": "asg_one",
                    "rx_group": "grp_missing",
                    "scan_list": "scan_missing",
                }
            ],
        )
        _write_radio(root / "radios")
        _write_ssrf(root / "ssrf", contacts=_FIXTURE_CONTACTS)

        with pytest.raises(ProfileValidationError) as excinfo:
            load_and_validate_profile(
                profile,
                [root / "ssrf"],
                radio_root=root / "radios",
            )

    message = str(excinfo.value)
    assert "unknown RX group list 'grp_missing'" in message
    assert "unknown scan list 'scan_missing'" in message
