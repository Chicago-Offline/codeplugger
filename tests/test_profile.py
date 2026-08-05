from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest
import yaml

from codeplugger.profile import ProfileValidationError, load_and_validate_profile
from codeplugger.resolved import ResolvedTones, resolve_codeplug


def _write_profile(path: Path, assignments: list[str]) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "id": "test_profile",
                "name": "Test profile",
                "radio": "test_radio",
                "zones": [
                    {
                        "id": "reference",
                        "name": "Reference",
                        "assignments": assignments,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_radio(root: Path, max_channels_per_zone: int = 2) -> None:
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
                },
            }
        ),
        encoding="utf-8",
    )


def _write_ssrf(root: Path) -> None:
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
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


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
            {"ctcss_tx_hz": 100.0, "ctcss_rx_hz": 123.0}
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
    assert resolved.channels[1].rx_frequency_mhz == 146.94
    assert resolved.channels[1].tx_frequency_mhz == 146.34
    assert resolved.channels[1].service == "amateur"
    assert resolved.channels[1].tones == ResolvedTones(
        ctcss_tx_hz=100.0,
        ctcss_rx_hz=123.0,
    )
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