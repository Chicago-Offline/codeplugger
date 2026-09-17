from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from codeplugger.backends.benlink import (
    BLANK_CHANNEL,
    BenlinkError,
    BenlinkTarget,
    BenlinkWriter,
    _settings_from_plan_entry,
)


VERIFIED = BenlinkTarget(device_name="VR-N76", hardware_verified=True)
UNVERIFIED = BenlinkTarget(device_name="UV-PRO")


class DCS(NamedTuple):
    n: int


class FakeRadio:
    """A Benshi radio with region-scoped channel tables, in memory."""

    def __init__(self, region_count: int = 2, channel_count: int = 3) -> None:
        self.device_info = SimpleNamespace(
            region_count=region_count,
            channel_count=channel_count,
            vendor_id=1,
            product_id=259,
        )
        self.status = SimpleNamespace(curr_region=1)
        self.region_names = ["Old A", "Old B"][:region_count]
        self._tables: dict[int, list[Any]] = {
            region: [_fake_channel(index) for index in range(channel_count)]
            for region in range(region_count)
        }
        self.region_switches: list[int] = []
        self.writes: list[tuple[int, int]] = []

    # RadioController surface used by the backend ------------------------

    @property
    def channels(self) -> list[Any]:
        return self._tables[self.status.curr_region]

    async def set_region(self, region_id: int) -> None:
        self.region_switches.append(region_id)
        self.status.curr_region = region_id

    async def get_region_name(self, region_id: int) -> str | None:
        return self.region_names[region_id]

    async def set_region_name(self, region_id: int, name: str) -> None:
        self.region_names[region_id] = name

    async def set_region_channel(
        self, region_id: int, channel_id: int, **channel_args: Any
    ) -> None:
        self.writes.append((region_id, channel_id))
        self._tables[region_id][channel_id] = SimpleNamespace(
            channel_id=channel_id, **channel_args
        )

    async def __aenter__(self) -> "FakeRadio":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _fake_channel(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        channel_id=index,
        name=f"OLD{index}",
        rx_freq=440.0 + index,
        tx_freq=440.0 + index,
        rx_mod="FM",
        tx_mod="FM",
        rx_sub_audio=None,
        tx_sub_audio=None,
        bandwidth="WIDE",
        scan=True,
        tx_disable=False,
        tx_at_max_power=True,
        tx_at_med_power=False,
        talk_around=False,
        pre_de_emph_bypass=False,
        sign=False,
        fixed_freq=False,
        fixed_bandwidth=False,
        fixed_tx_power=False,
        mute=False,
    )


def _plan(**entry_overrides: Any) -> dict[str, Any]:
    entry = {
        "slot": 1,
        "name": "2m Call",
        "rx_mhz": 146.52,
        "tx_mhz": 146.52,
        "bandwidth": "WIDE",
        "power": "high",
        "scan": True,
        "tx_disable": False,
        "tx_tone": {"ctcss": 100.0},
    }
    entry.update(entry_overrides)
    return {
        "format": "benlink-codeplug",
        "version": 1,
        "name": "n76_01",
        "radio": {"model": "vero_vrn76", "vendor_id": 1, "product_id": 259},
        "regions": [
            {
                "index": 0,
                "name": "Ham",
                "blank_unlisted": True,
                "channels": [entry],
            }
        ],
    }


@pytest.fixture
def radio(monkeypatch: pytest.MonkeyPatch) -> FakeRadio:
    fake = FakeRadio()
    monkeypatch.setattr(
        "codeplugger.backends.benlink._benlink",
        lambda: (
            SimpleNamespace(
                RadioController=SimpleNamespace(
                    new_ble=lambda address: fake,
                    new_rfcomm=lambda address: fake,
                )
            ),
            SimpleNamespace(DCS=DCS),
        ),
    )
    return fake


def test_write_applies_the_plan_blanks_the_rest_and_reads_back_clean(
    radio: FakeRadio, tmp_path: Path
) -> None:
    result = BenlinkWriter().write(
        _plan(),
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    assert result["applied"] is True
    assert result["mismatches"] == []
    assert radio.region_names[0] == "Ham"

    written = radio._tables[0]
    assert written[0].name == "2m Call"
    assert written[0].tx_sub_audio == 100.0
    # Slots the plan does not fill are erased rather than left behind.
    assert [channel.name for channel in written[1:]] == ["", ""]
    assert written[1].rx_freq == 0.0
    assert radio.writes == [(0, 0), (0, 1), (0, 2)]

    # Region 1 is untouched: this backend owns only the regions it was given.
    assert [channel.name for channel in radio._tables[1]] == ["OLD0", "OLD1", "OLD2"]

    # Reading another region means switching to it, so the radio has to be put
    # back where it started.
    assert radio.status.curr_region == 1


def test_write_takes_a_backup_before_touching_anything(
    radio: FakeRadio, tmp_path: Path
) -> None:
    backup = tmp_path / "before.json"
    BenlinkWriter().write(
        _plan(),
        "peripheral-uuid",
        target=VERIFIED,
        backup=backup,
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    document = json.loads(backup.read_text(encoding="utf-8"))
    assert document["regions"]["0"]["name"] == "Old A"
    assert document["regions"]["0"]["channels"]["0"]["name"] == "OLD0"


def test_dcs_sub_audio_round_trips_through_the_protocol_type(
    radio: FakeRadio, tmp_path: Path
) -> None:
    result = BenlinkWriter().write(
        _plan(tx_tone={"dcs": 23}, rx_tone={"dcs": 23}),
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    assert result["mismatches"] == []
    assert radio._tables[0][0].tx_sub_audio == DCS(n=23)


def test_read_back_mismatch_is_raised_not_returned(
    radio: FakeRadio, tmp_path: Path
) -> None:
    original = radio.set_region_channel

    async def drop_the_name(region_id: int, channel_id: int, **args: Any) -> None:
        await original(region_id, channel_id, **{**args, "name": "WRONG"})

    radio.set_region_channel = drop_the_name  # type: ignore[method-assign]

    with pytest.raises(BenlinkError, match="read-back differs"):
        BenlinkWriter().write(
            _plan(),
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_write_refuses_a_plan_larger_than_the_radio_reports(
    radio: FakeRadio, tmp_path: Path
) -> None:
    plan = _plan()
    plan["regions"][0]["channels"][0]["slot"] = 8

    with pytest.raises(BenlinkError, match="channel slots"):
        BenlinkWriter().write(
            plan,
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_write_refuses_without_confirm(radio: FakeRadio, tmp_path: Path) -> None:
    with pytest.raises(BenlinkError, match="confirm=True"):
        BenlinkWriter().write(
            _plan(),
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
        )


def test_write_refuses_an_unverified_model_without_an_opt_in(
    radio: FakeRadio, tmp_path: Path
) -> None:
    with pytest.raises(BenlinkError, match="hardware_verified"):
        BenlinkWriter().write(
            _plan(),
            "peripheral-uuid",
            target=UNVERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_write_refuses_to_skip_verification_silently(
    radio: FakeRadio, tmp_path: Path
) -> None:
    with pytest.raises(BenlinkError, match="read_back path"):
        BenlinkWriter().write(
            _plan(),
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            confirm=True,
        )


def test_write_refuses_to_overwrite_an_existing_backup(
    radio: FakeRadio, tmp_path: Path
) -> None:
    backup = tmp_path / "before.json"
    backup.write_text("{}", encoding="utf-8")

    with pytest.raises(BenlinkError, match="already exists"):
        BenlinkWriter().write(
            _plan(),
            "peripheral-uuid",
            target=VERIFIED,
            backup=backup,
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_target_refuses_a_radio_without_a_benlink_block() -> None:
    with pytest.raises(BenlinkError, match="no benlink capabilities block"):
        BenlinkTarget.from_capabilities({"id": "retevis_c64"})


def test_target_reads_the_capabilities_block() -> None:
    target = BenlinkTarget.from_capabilities(
        {
            "id": "vero_vrn76",
            "benlink": {
                "device_name": "VR-N76",
                "transport": "rfcomm",
                "hardware_verified": True,
            },
        }
    )
    assert target == BenlinkTarget("VR-N76", "rfcomm", True)


@pytest.mark.parametrize("transport", ["ble", "rfcomm"])
def test_transport_picks_the_matching_controller(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    used: list[str] = []
    monkeypatch.setattr(
        "codeplugger.backends.benlink._benlink",
        lambda: (
            SimpleNamespace(
                RadioController=SimpleNamespace(
                    new_ble=lambda address: used.append("ble"),
                    new_rfcomm=lambda address: used.append("rfcomm"),
                )
            ),
            SimpleNamespace(DCS=DCS),
        ),
    )

    BenlinkWriter()._controller("addr", BenlinkTarget("VR-N76", transport))
    assert used == [transport]


def test_unknown_transport_is_refused(radio: FakeRadio) -> None:
    with pytest.raises(BenlinkError, match="unknown benlink transport"):
        BenlinkWriter()._controller("addr", BenlinkTarget("VR-N76", "usb"))


def test_blank_channel_covers_every_field_a_plan_can_set() -> None:
    # The read-back diff compares an unfilled slot against BLANK_CHANNEL, so a
    # field missing from it would never be checked on erased slots.
    entry = _plan()["regions"][0]["channels"][0]
    assert set(_settings_from_plan_entry(entry)) <= set(BLANK_CHANNEL)


def test_plan_slots_are_one_based_and_channel_ids_are_not(
    radio: FakeRadio, tmp_path: Path
) -> None:
    BenlinkWriter().write(
        _plan(slot=2, name="Slot Two"),
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    assert radio._tables[0][1].name == "Slot Two"
    assert radio._tables[0][0].name == ""


def test_blank_unlisted_false_leaves_other_slots_alone(
    radio: FakeRadio, tmp_path: Path
) -> None:
    plan = _plan()
    plan["regions"][0]["blank_unlisted"] = False

    BenlinkWriter().write(
        plan,
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    assert radio.writes == [(0, 0)]
    assert [channel.name for channel in radio._tables[0][1:]] == ["OLD1", "OLD2"]


@pytest.mark.parametrize(
    ("power", "at_max", "at_med"),
    [("high", True, False), ("med", False, True), ("low", False, False)],
)
def test_plan_power_becomes_the_radios_two_power_bits(
    radio: FakeRadio, tmp_path: Path, power: str, at_max: bool, at_med: bool
) -> None:
    BenlinkWriter().write(
        _plan(power=power),
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    written = radio._tables[0][0]
    assert (written.tx_at_max_power, written.tx_at_med_power) == (at_max, at_med)


def test_am_modulation_applies_to_both_directions(
    radio: FakeRadio, tmp_path: Path
) -> None:
    BenlinkWriter().write(
        _plan(modulation="AM", tx_mhz=0.0, tx_disable=True),
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )

    written = radio._tables[0][0]
    assert (written.rx_mod, written.tx_mod) == ("AM", "AM")


def test_write_refuses_a_plan_from_a_future_format_version(
    radio: FakeRadio, tmp_path: Path
) -> None:
    plan = _plan()
    plan["version"] = 99

    with pytest.raises(BenlinkError, match="plan version 99"):
        BenlinkWriter().write(
            plan,
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_write_refuses_a_radio_reporting_another_product_id(
    radio: FakeRadio, tmp_path: Path
) -> None:
    plan = _plan()
    plan["radio"]["product_id"] = 260

    with pytest.raises(BenlinkError, match="product_id 260"):
        BenlinkWriter().write(
            plan,
            "peripheral-uuid",
            target=VERIFIED,
            backup=tmp_path / "before.json",
            read_back=tmp_path / "after.json",
            confirm=True,
        )


def test_a_plan_without_a_product_id_falls_back_to_the_count_check(
    radio: FakeRadio, tmp_path: Path
) -> None:
    # Radios nobody has connected yet have no id to check, and must still be
    # programmable behind the other gates.
    plan = _plan()
    plan["radio"].pop("product_id")

    result = BenlinkWriter().write(
        plan,
        "peripheral-uuid",
        target=VERIFIED,
        backup=tmp_path / "before.json",
        read_back=tmp_path / "after.json",
        confirm=True,
    )
    assert result["mismatches"] == []
