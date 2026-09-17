"""Program Benshi radios over Bluetooth through the ``benlink`` library.

Benshi handhelds -- Vero VR-N76, BTECH UV-Pro, RadioOddity GA-5WB and their
relatives -- ship no programming cable and have no CHIRP driver, because CHIRP
has no Bluetooth transport at all. The only documented way in is the BLE/RFCOMM
protocol reverse-engineered by `benlink <https://github.com/khusmann/benlink>`_.

Unlike ``chirp-writer``, this backend imports its dependency instead of running
a subprocess: benlink is Apache-2.0, the same licence as codeplugger, so there
is no boundary to keep. It is an optional import all the same, so that
codeplugger stays installable without a Bluetooth stack.

The safety model differs from the cable-based backends in two ways worth
stating plainly:

- **There is no image.** Every other backend downloads a codeplug blob, writes
  one back, and diffs bytes. Here each channel is a separate round trip, so a
  failed write leaves the radio half-programmed. The mandatory backup is a
  JSON snapshot of every region this backend can see, taken before the first
  write, and it is what a recovery is rebuilt from.

- **The identity gate is what the radio reports about itself.** There is no
  model-string check equivalent to CHIRP's. Instead the connected radio's
  ``DevInfo`` region and channel counts must cover the plan, and a radio whose
  capabilities entry is not marked hardware-verified is refused unless the
  caller opts in. That is deliberately weak, which is why the experimental
  family members are gated rather than merely documented.

Region and channel numbering is 0-based here and 1-based in the radio's own
UI, so region 0 is the group the radio calls Group 1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..artifacts import ArtifactStore
from ..exporters.benlink_plan import PLAN_FORMAT, PLAN_VERSION

# Channel-record fields the codeplug has no opinion about. They are set on
# every write so a slot's content depends only on the plan, never on whatever
# the previous occupant of that slot left behind.
CHANNEL_DEFAULTS: dict[str, Any] = {
    "tx_at_max_power": True,
    "tx_at_med_power": False,
    "talk_around": False,
    "pre_de_emph_bypass": False,
    "sign": False,
    "fixed_freq": False,
    "fixed_bandwidth": False,
    "fixed_tx_power": False,
    "mute": False,
}

# An erased slot. Zero frequencies and an empty name are how the radio's own
# app clears a channel.
BLANK_CHANNEL: dict[str, Any] = {
    **CHANNEL_DEFAULTS,
    "name": "",
    "rx_freq": 0.0,
    "tx_freq": 0.0,
    "rx_mod": "FM",
    "tx_mod": "FM",
    "rx_sub_audio": None,
    "tx_sub_audio": None,
    "bandwidth": "WIDE",
    "scan": False,
    "tx_disable": False,
}

# Frequencies cross the wire as microhertz-scaled integers, so comparing
# beyond six decimals compares rounding noise.
FREQUENCY_DIGITS = 6


class BenlinkError(RuntimeError):
    """Raised when a Benshi radio cannot be programmed as asked."""


def _benlink():
    """Import benlink on demand, with an actionable error when it is missing."""

    try:
        from benlink import command, controller
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise BenlinkError(
            "benlink is not installed; install codeplugger with the 'benlink' "
            "extra to program Benshi radios over Bluetooth"
        ) from exc
    return controller, command


@dataclass(frozen=True)
class BenlinkTarget:
    """How the benlink backend reaches and identifies one radio model."""

    device_name: str
    transport: str = "rfcomm"
    hardware_verified: bool = False

    @classmethod
    def from_capabilities(cls, capabilities: Mapping[str, Any]) -> "BenlinkTarget":
        """Build a target from a radio's ``capabilities.json`` ``benlink`` block.

        A radio without that block is not addressable over Bluetooth; refusing
        here is the same stance the CHIRP backend takes about a missing driver.
        """

        block = capabilities.get("benlink")
        if not block:
            raise BenlinkError(
                f"radio '{capabilities.get('id', '?')}' has no benlink capabilities "
                "block; it cannot be programmed over Bluetooth"
            )
        return cls(
            device_name=block["device_name"],
            transport=block.get("transport", "rfcomm"),
            hardware_verified=block.get("hardware_verified", False),
        )


def _sub_audio_to_plan(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    code = getattr(value, "n", None)
    if code is not None:
        return {"dcs": int(code)}
    return {"ctcss_hz": float(value)}


def _sub_audio_from_plan(value: Mapping[str, Any] | None, dcs_type: Any) -> Any:
    if value is None:
        return None
    if "dcs" in value:
        return dcs_type(n=int(value["dcs"]))
    return float(value["ctcss_hz"])


def _settings_from_channel(channel: Any) -> dict[str, Any]:
    """Project a benlink ``Channel`` onto the plan's settings shape."""

    settings = {
        "name": channel.name,
        "rx_freq": round(float(channel.rx_freq), FREQUENCY_DIGITS),
        "tx_freq": round(float(channel.tx_freq), FREQUENCY_DIGITS),
        "rx_mod": channel.rx_mod,
        "tx_mod": channel.tx_mod,
        "rx_sub_audio": _sub_audio_to_plan(channel.rx_sub_audio),
        "tx_sub_audio": _sub_audio_to_plan(channel.tx_sub_audio),
        "bandwidth": channel.bandwidth,
        "scan": channel.scan,
        "tx_disable": channel.tx_disable,
    }
    settings.update(
        {name: getattr(channel, name) for name in CHANNEL_DEFAULTS}
    )
    return settings


def _channel_args(settings: Mapping[str, Any], dcs_type: Any) -> dict[str, Any]:
    """Turn plan settings into the full keyword set benlink writes.

    Every field is passed, including the ones the plan does not mention, so
    ``set_region_channel`` has nothing left to inherit from the slot's current
    contents.
    """

    args = {**CHANNEL_DEFAULTS, **settings}
    args["rx_freq"] = round(float(args["rx_freq"]), FREQUENCY_DIGITS)
    args["tx_freq"] = round(float(args["tx_freq"]), FREQUENCY_DIGITS)
    args["rx_sub_audio"] = _sub_audio_from_plan(args["rx_sub_audio"], dcs_type)
    args["tx_sub_audio"] = _sub_audio_from_plan(args["tx_sub_audio"], dcs_type)
    return args


def _intended_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a read-back should find, in comparable form."""

    intended = {**CHANNEL_DEFAULTS, **settings}
    intended["rx_freq"] = round(float(intended["rx_freq"]), FREQUENCY_DIGITS)
    intended["tx_freq"] = round(float(intended["tx_freq"]), FREQUENCY_DIGITS)
    return intended


# The exporter's ``benlink-codeplug`` document is the on-disk contract; the
# writer below works in the radio's own vocabulary. Everything from here to
# ``normalize_plan`` translates between the two, so the plan format can grow
# without the wire code learning about it.

# The radio has two power bits rather than a three-value field, so "low" is
# the absence of both.
POWER_FLAGS: dict[str, dict[str, bool]] = {
    "high": {"tx_at_max_power": True, "tx_at_med_power": False},
    "med": {"tx_at_max_power": False, "tx_at_med_power": True},
    "low": {"tx_at_max_power": False, "tx_at_med_power": False},
}


def _tone_to_sub_audio(tone: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if tone is None:
        return None
    if "dcs" in tone:
        return {"dcs": int(tone["dcs"])}
    return {"ctcss_hz": float(tone["ctcss"])}


def _settings_from_plan_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    power = str(entry.get("power", "high")).lower()
    if power not in POWER_FLAGS:
        raise BenlinkError(
            f"channel '{entry.get('name', '?')}' has power {power!r}; "
            f"expected one of {', '.join(POWER_FLAGS)}"
        )
    # The plan omits modulation entirely for the FM case, and carries one
    # value where the radio keeps a separate bit per direction.
    modulation = str(entry.get("modulation", "FM")).upper()
    return {
        **CHANNEL_DEFAULTS,
        **POWER_FLAGS[power],
        "name": entry["name"],
        "rx_freq": float(entry["rx_mhz"]),
        "tx_freq": float(entry["tx_mhz"]),
        "rx_mod": modulation,
        "tx_mod": modulation,
        "rx_sub_audio": _tone_to_sub_audio(entry.get("rx_tone")),
        "tx_sub_audio": _tone_to_sub_audio(entry.get("tx_tone")),
        "bandwidth": entry.get("bandwidth", "WIDE"),
        "scan": bool(entry.get("scan", True)),
        "tx_disable": bool(entry.get("tx_disable", False)),
        "mute": bool(entry.get("mute", False)),
        "talk_around": bool(entry.get("talk_around", False)),
    }


def normalize_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a ``benlink-codeplug`` plan into the writer's region shape.

    Slots are 1-based in the plan because that is the number on the radio's
    dial; channel IDs are 0-based on the wire. Doing the shift here, once,
    keeps the off-by-one out of the write and verify paths.
    """

    format_name = plan.get("format", PLAN_FORMAT)
    if format_name != PLAN_FORMAT:
        raise BenlinkError(
            f"plan format {format_name!r} is not {PLAN_FORMAT!r}"
        )
    version = int(plan.get("version", PLAN_VERSION))
    if version != PLAN_VERSION:
        raise BenlinkError(
            f"plan version {version} is not supported (expected {PLAN_VERSION})"
        )

    regions: list[dict[str, Any]] = []
    for region in plan["regions"]:
        channels: list[dict[str, Any]] = []
        for entry in region["channels"]:
            slot = int(entry["slot"])
            if slot < 1:
                raise BenlinkError(
                    f"channel '{entry.get('name', '?')}' has slot {slot}; "
                    "plan slots are numbered from 1"
                )
            channels.append(
                {
                    "channel_id": slot - 1,
                    "settings": _settings_from_plan_entry(entry),
                }
            )
        regions.append(
            {
                "region_id": int(region["index"]),
                "name": region["name"],
                "blank_unlisted": bool(region.get("blank_unlisted", True)),
                "channels": channels,
            }
        )

    radio = plan.get("radio") or {}
    return {
        "radio": radio.get("model"),
        "radio_instance": plan.get("name"),
        "vendor_id": radio.get("vendor_id"),
        "product_id": radio.get("product_id"),
        "regions": regions,
    }


@dataclass(frozen=True)
class BenlinkWriter:
    """Gated programming operations for one Benshi radio."""

    artifact_store: ArtifactStore | None = None
    connect_timeout: float = 20.0

    # -- connection -------------------------------------------------------

    def _controller(self, address: str, target: BenlinkTarget) -> Any:
        controller, _ = _benlink()
        if target.transport == "rfcomm":
            return controller.RadioController.new_rfcomm(address)
        if target.transport != "ble":
            raise BenlinkError(f"unknown benlink transport '{target.transport}'")
        return controller.RadioController.new_ble(address)

    async def discover_async(
        self, target: BenlinkTarget, *, timeout: float = 12.0
    ) -> list[tuple[str, str]]:
        """Return ``(address, name)`` for every advertising radio that matches.

        On macOS the address is a CoreBluetooth peripheral UUID rather than the
        MAC the radio prints on itself, which is why discovery exists instead
        of asking an operator to type an address.
        """

        try:
            from bleak import BleakScanner
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BenlinkError(
                "bleak is not installed; install codeplugger with the 'benlink' "
                "extra to discover Bluetooth radios"
            ) from exc

        wanted = target.device_name.upper()
        found = await BleakScanner.discover(timeout=timeout)
        return [
            (device.address, device.name)
            for device in found
            if device.name and wanted in device.name.upper()
        ]

    def discover(
        self, target: BenlinkTarget, *, timeout: float = 12.0
    ) -> list[tuple[str, str]]:
        """Blocking wrapper around :meth:`discover_async`."""

        return asyncio.run(self.discover_async(target, timeout=timeout))

    # -- identity ---------------------------------------------------------

    @staticmethod
    def _check_capacity(radio: Any, plan: Mapping[str, Any]) -> dict[str, int]:
        """Refuse a plan the connected radio is not the target of.

        A declared ``product_id`` is the closest thing to CHIRP's model-string
        gate available here, so a mismatch is fatal. It is often absent -- the
        id has to be read off a physical radio -- and the fallback is weaker:
        a radio reporting fewer regions or slots than the plan needs is either
        the wrong model or the wrong firmware, and either way writing would
        silently drop channels.
        """

        info = radio.device_info

        for key in ("vendor_id", "product_id"):
            expected = plan.get(key)
            observed = getattr(info, key, None)
            if expected is not None and observed is not None and observed != expected:
                raise BenlinkError(
                    f"plan targets {key} {expected} but the connected radio "
                    f"reports {observed}"
                )

        regions = plan["regions"]
        needed_regions = max((region["region_id"] for region in regions), default=-1) + 1
        needed_slots = max(
            (
                channel["channel_id"] + 1
                for region in regions
                for channel in region["channels"]
            ),
            default=0,
        )
        if needed_regions > info.region_count:
            raise BenlinkError(
                f"plan needs {needed_regions} regions but the radio reports "
                f"{info.region_count}"
            )
        if needed_slots > info.channel_count:
            raise BenlinkError(
                f"plan needs {needed_slots} channel slots per region but the "
                f"radio reports {info.channel_count}"
            )
        return {
            "region_count": info.region_count,
            "channel_count": info.channel_count,
        }

    # -- backup and read-back --------------------------------------------

    @staticmethod
    async def _snapshot(radio: Any, region_ids: Sequence[int]) -> dict[str, Any]:
        """Read whole regions back, restoring the active region afterwards.

        There is no "read channel N of region R" command: the only way to see
        another region's table is to switch to it, so a snapshot moves the
        radio's current group and has to put it back.
        """

        original = radio.status.curr_region
        slots = radio.device_info.channel_count
        regions: dict[str, Any] = {}
        try:
            for region_id in region_ids:
                if region_id != radio.status.curr_region:
                    await radio.set_region(region_id)
                regions[str(region_id)] = {
                    "name": await radio.get_region_name(region_id),
                    "channels": {
                        str(index): _settings_from_channel(radio.channels[index])
                        for index in range(slots)
                    },
                }
        finally:
            if radio.status.curr_region != original:
                await radio.set_region(original)
        return regions

    @staticmethod
    def _diff(
        plan: Mapping[str, Any], snapshot: Mapping[str, Any], slots: int
    ) -> list[dict[str, Any]]:
        """Compare a read-back snapshot against what the plan asked for."""

        mismatches: list[dict[str, Any]] = []
        for region in plan["regions"]:
            region_id = region["region_id"]
            observed = snapshot.get(str(region_id), {})
            observed_channels = observed.get("channels", {})

            if observed.get("name") != region["name"]:
                mismatches.append(
                    {
                        "region_id": region_id,
                        "field": "name",
                        "want": region["name"],
                        "got": observed.get("name"),
                    }
                )

            intended = {
                channel["channel_id"]: _intended_settings(channel["settings"])
                for channel in region["channels"]
            }
            for channel_id in range(slots):
                if channel_id not in intended and not region["blank_unlisted"]:
                    continue
                want = intended.get(channel_id, BLANK_CHANNEL)
                got = observed_channels.get(str(channel_id), {})
                for field, value in want.items():
                    if got.get(field) != value:
                        mismatches.append(
                            {
                                "region_id": region_id,
                                "channel_id": channel_id,
                                "field": field,
                                "want": value,
                                "got": got.get(field),
                            }
                        )
        return mismatches

    # -- write ------------------------------------------------------------

    async def write_async(
        self,
        plan: Mapping[str, Any],
        address: str,
        *,
        target: BenlinkTarget,
        backup: Path,
        read_back: Path | None = None,
        confirm: bool = False,
        verify: bool = True,
        allow_unverified: bool = False,
    ) -> dict[str, Any]:
        """Apply a benlink region plan to a radio, taking a backup first.

        Gates, in order: ``confirm=True``; an unverified radio model requires
        ``allow_unverified=True``; ``verify`` requires somewhere to store the
        read-back; the backup path must not already exist; and the connected
        radio must report enough regions and slots for the plan.

        ``plan`` is a ``benlink-codeplug`` document as produced by
        :mod:`codeplugger.exporters.benlink_plan`.

        Only the regions the plan names are touched. Within those, slots the
        plan does not fill are erased unless the region sets
        ``blank_unlisted`` false, so a region ends up holding exactly what the
        profile says and nothing a previous codeplug left behind.
        """

        if not confirm:
            raise BenlinkError("refusing to write without confirm=True")
        if not target.hardware_verified and not allow_unverified:
            raise BenlinkError(
                f"'{target.device_name}' is not marked hardware_verified in its "
                "capabilities; pass allow_unverified=True to program it anyway"
            )
        if verify and read_back is None:
            raise BenlinkError(
                "refusing to write without a read_back path; pass verify=False "
                "only when a read-back is impossible"
            )
        if backup.exists():
            raise BenlinkError(f"backup path '{backup}' already exists")

        _, command = _benlink()
        plan = normalize_plan(plan)
        region_ids = [region["region_id"] for region in plan["regions"]]
        artifacts = [path for path in (backup, read_back) if path is not None]
        result: dict[str, Any] = {
            "radio": plan.get("radio"),
            "radio_instance": plan.get("radio_instance"),
            "address": address,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "regions": region_ids,
            "applied": False,
        }

        try:
            async with self._controller(address, target) as radio:
                result["device"] = self._check_capacity(radio, plan)
                slots = radio.device_info.channel_count

                snapshot = await self._snapshot(radio, region_ids)
                self._write_json(backup, self._snapshot_document(address, snapshot))

                await self._apply(radio, plan, command.DCS, slots)
                result["applied"] = True

                if read_back is not None:
                    observed = await self._snapshot(radio, region_ids)
                    self._write_json(
                        read_back, self._snapshot_document(address, observed)
                    )
                    result["mismatches"] = self._diff(plan, observed, slots)
        except BenlinkError:
            self._record("write", "failure", artifacts, None)
            raise
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised
            detail = f"{type(exc).__name__}: {exc}"
            self._record(
                "write", "failure" if not result["applied"] else "partial",
                artifacts, detail,
            )
            raise BenlinkError(f"benlink write failed: {detail}") from exc

        mismatches = result.get("mismatches")
        if mismatches:
            self._record(
                "write", "mismatch", artifacts, f"{len(mismatches)} field(s) differ"
            )
            raise BenlinkError(
                f"read-back differs from the plan in {len(mismatches)} field(s); "
                f"see {read_back}"
            )

        self._record("write", "success", artifacts, None)
        return result

    def write(
        self,
        plan: Mapping[str, Any],
        address: str,
        *,
        target: BenlinkTarget,
        backup: Path,
        read_back: Path | None = None,
        confirm: bool = False,
        verify: bool = True,
        allow_unverified: bool = False,
    ) -> dict[str, Any]:
        """Blocking wrapper around :meth:`write_async`."""

        return asyncio.run(
            self.write_async(
                plan,
                address,
                target=target,
                backup=backup,
                read_back=read_back,
                confirm=confirm,
                verify=verify,
                allow_unverified=allow_unverified,
            )
        )

    async def backup_async(
        self,
        address: str,
        *,
        target: BenlinkTarget,
        output: Path,
        region_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Snapshot regions to JSON without writing anything to the radio."""

        if output.exists():
            raise BenlinkError(f"backup path '{output}' already exists")

        async with self._controller(address, target) as radio:
            ids = (
                list(region_ids)
                if region_ids is not None
                else list(range(radio.device_info.region_count))
            )
            document = self._snapshot_document(
                address, await self._snapshot(radio, ids)
            )
        self._write_json(output, document)
        self._record("backup", "success", (output,), None)
        return document

    def backup(
        self,
        address: str,
        *,
        target: BenlinkTarget,
        output: Path,
        region_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Blocking wrapper around :meth:`backup_async`."""

        return asyncio.run(
            self.backup_async(
                address, target=target, output=output, region_ids=region_ids
            )
        )

    # -- internals --------------------------------------------------------

    @staticmethod
    async def _apply(
        radio: Any, plan: Mapping[str, Any], dcs_type: Any, slots: int
    ) -> None:
        for region in plan["regions"]:
            region_id = region["region_id"]
            await radio.set_region_name(region_id, region["name"])

            filled = {
                channel["channel_id"]: channel["settings"]
                for channel in region["channels"]
            }
            for channel_id in range(slots):
                if channel_id not in filled and not region["blank_unlisted"]:
                    continue
                settings = filled.get(channel_id, BLANK_CHANNEL)
                await radio.set_region_channel(
                    region_id, channel_id, **_channel_args(settings, dcs_type)
                )

    @staticmethod
    def _snapshot_document(
        address: str, regions: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "address": address,
            "read_at": datetime.now(timezone.utc).isoformat(),
            "regions": regions,
        }

    @staticmethod
    def _write_json(path: Path, document: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _record(
        self,
        operation: str,
        status: str,
        artifacts: Sequence[Path],
        detail: str | None,
    ) -> None:
        if self.artifact_store is None:
            return
        self.artifact_store.record(
            operation, status, artifacts=artifacts, detail=detail
        )
