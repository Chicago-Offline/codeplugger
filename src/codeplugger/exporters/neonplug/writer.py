"""Build a NeonPlug ``.neonplug`` archive from a resolved analog codeplug.

Profile 0.1 scope only: analog FM channels and zones. Digital/DMR channels,
scan lists, contacts, and button settings are rejected or left untouched --
see ``channel_defaults.py`` and the package docstring for what is emitted and
why.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ...resolved import ResolvedChannel, ResolvedCodeplug, ResolvedZone

from .channel_defaults import (
    CHANNEL_MAX,
    CHANNEL_NAME_MAX_CHARS,
    CODEPLUG_JSON_FILENAME,
    DEFAULT_CHANNEL_FIELDS,
    FORMAT_VERSION,
    ZONE_CHANNELS_MAX,
    ZONE_NAME_MAX_CHARS,
    ZONES_MAX,
)

# Power/bandwidth policy: NeonPlug's DM-32UV Channel model only has three
# discrete power levels (Channel.ts `power: 'Low' | 'Medium' | 'High'`) and
# two bandwidths (`bandwidth: '12.5kHz' | '25kHz'`). Mapping a resolved
# codeplug's numeric watts/kHz onto those enums is this exporter's own
# explicit, reviewed policy -- not a value inherited from a cloned template
# or personal export. These thresholds mirror this repo's qdmr_yaml exporter
# for consistency across backends; there is no NeonPlug-defined threshold to
# cite because NeonPlug never converts watts, a human always picks the enum
# directly in its UI.
DEFAULT_POWER = "High"
POWER_LOW_MAX_W = 1.0
POWER_MID_MAX_W = 2.5


def _power(power_w: float | None) -> str:
    if power_w is None:
        return DEFAULT_POWER
    if power_w <= POWER_LOW_MAX_W:
        return "Low"
    if power_w <= POWER_MID_MAX_W:
        return "Medium"
    return "High"


def _bandwidth(bandwidth_khz: float, *, channel_label: str) -> str:
    if bandwidth_khz == 12.5:
        return "12.5kHz"
    if bandwidth_khz == 25.0:
        return "25kHz"
    raise ValueError(
        f"channel '{channel_label}' bandwidth {bandwidth_khz} kHz is not "
        "representable in NeonPlug's DM-32UV model (only 12.5kHz or 25kHz)"
    )


def _ctcss_dcs(
    ctcss_hz: float | None,
    dcs_code: str | int | None,
    *,
    side: str,
    channel_label: str,
) -> dict[str, Any]:
    """Build a NeonPlug ``CTCSSDCS`` value: ``{type, value?, polarity?}``.

    Source: ``src/models/Channel.ts`` (``CTCSSDCS`` shape) and
    ``src/utils/ctcssConstants.ts`` (``value`` is CTCSS Hz or the decimal DCS
    code, ``polarity`` is ``'N'`` normal / ``'P'`` inverted).
    """
    if ctcss_hz is not None and dcs_code is not None:
        raise ValueError(
            f"channel '{channel_label}' has both CTCSS and DCS set on {side}; "
            "NeonPlug's CTCSSDCS field can only represent one at a time"
        )
    if ctcss_hz is not None:
        return {"type": "CTCSS", "value": round(float(ctcss_hz), 1)}
    if dcs_code is not None:
        text = str(dcs_code).strip().upper()
        if text.startswith("D"):
            text = text[1:]
        polarity = "N"
        if text.endswith("I"):
            polarity = "P"
            text = text[:-1]
        elif text.endswith("N"):
            polarity = "N"
            text = text[:-1]
        if not text.isdigit():
            raise ValueError(
                f"channel '{channel_label}' has an unsupported DCS code "
                f"{dcs_code!r} on {side}"
            )
        return {"type": "DCS", "value": int(text), "polarity": polarity}
    return {"type": "None"}


def _checked_name(raw: str | None, *, max_chars: int, kind: str, label: str) -> str:
    name = raw or ""
    if not name.isascii():
        raise ValueError(
            f"{kind} '{label}' name {name!r} has non-ASCII characters; "
            "NeonPlug's DM-32UV encoder writes/reads names as raw ASCII bytes"
        )
    if len(name) > max_chars:
        raise ValueError(
            f"{kind} '{label}' name {name!r} is {len(name)} chars, exceeds "
            f"NeonPlug's {max_chars}-char limit for this radio"
        )
    return name


def _frequencies(channel: ResolvedChannel) -> tuple[float, float, bool]:
    rx = channel.rx_frequency_mhz
    tx = channel.tx_frequency_mhz
    forbid_tx = (not channel.tx_permitted) or tx is None
    # NeonPlug's encoder always writes a numeric txFrequency, even when
    # transmit is forbidden (only the forbidTx flag, not the stored
    # frequency, governs whether the radio will key up). Reusing rx avoids
    # inventing a transmittable frequency for receive-only channels; this
    # mirrors this repo's qdmr_yaml exporter's policy.
    return rx, (tx if tx is not None else rx), forbid_tx


def _channel_document(
    channel: ResolvedChannel,
    number: int,
    *,
    analog_bandwidth_khz: float | None,
) -> dict[str, Any]:
    if not channel.reference:
        raise ValueError(f"channel '{channel.display_name}' has no stable reference")

    mode = (channel.mode or "FM").upper()
    if mode != "FM":
        # Profile 0.1 scope is analog FM only. NeonPlug's Channel.mode has no
        # AM value at all (only 'Analog' | 'Digital' | 'Fixed Analog' |
        # 'Fixed Digital') -- the DM-32UV encoder infers receive-only
        # aviation-band behavior from frequency range, not from a stored
        # mode (src/radios/dm32uv/structures.ts isRxInNoTxBand()). Without a
        # verified test of that path this exporter rejects AM/digital rather
        # than guessing at aviation-band thresholds.
        raise ValueError(
            f"channel '{channel.display_name}' mode '{channel.mode}' is not "
            "supported by the NeonPlug exporter (profile 0.1 covers analog "
            "FM channels only)"
        )

    bandwidth_khz = channel.bandwidth_khz if channel.bandwidth_khz is not None else analog_bandwidth_khz
    if bandwidth_khz is None:
        raise ValueError(
            f"channel '{channel.display_name}' has no bandwidth, and no "
            "analog_bandwidth_khz default was given to the exporter"
        )

    rx, tx, forbid_tx = _frequencies(channel)
    name = _checked_name(
        channel.display_name,
        max_chars=CHANNEL_NAME_MAX_CHARS,
        kind="channel",
        label=channel.display_name,
    )

    document: dict[str, Any] = dict(DEFAULT_CHANNEL_FIELDS)
    document.update(
        {
            "number": number,
            "name": name,
            "rxFrequency": round(float(rx), 4),
            "txFrequency": round(float(tx), 4),
            "mode": "Analog",
            "forbidTx": forbid_tx,
            "bandwidth": _bandwidth(bandwidth_khz, channel_label=channel.display_name),
            "power": _power(channel.power_w),
            "rxCtcssDcs": _ctcss_dcs(
                channel.tones.ctcss_rx_hz if channel.tones else None,
                channel.tones.dcs_rx_code if channel.tones else None,
                side="rx",
                channel_label=channel.display_name,
            ),
            "txCtcssDcs": _ctcss_dcs(
                channel.tones.ctcss_tx_hz if channel.tones else None,
                channel.tones.dcs_tx_code if channel.tones else None,
                side="tx",
                channel_label=channel.display_name,
            ),
        }
    )
    return document


def _zone_document(zone: ResolvedZone, channel_numbers: dict[str, int]) -> dict[str, Any]:
    if len(zone.channel_references) > ZONE_CHANNELS_MAX:
        raise ValueError(
            f"zone '{zone.name}' has {len(zone.channel_references)} channels, "
            f"exceeds NeonPlug's {ZONE_CHANNELS_MAX}-channel-per-zone limit"
        )

    numbers: list[int] = []
    seen: set[str] = set()
    for reference in zone.channel_references:
        if reference in seen:
            raise ValueError(
                f"zone '{zone.name}' references channel {reference!r} more than once"
            )
        seen.add(reference)
        try:
            numbers.append(channel_numbers[reference])
        except KeyError:
            raise ValueError(
                f"zone '{zone.name}' references channel {reference!r}, which is "
                "not the stable reference of any channel in this codeplug"
            ) from None

    name = _checked_name(zone.name, max_chars=ZONE_NAME_MAX_CHARS, kind="zone", label=zone.name)
    return {"id": zone.id, "name": name, "channels": numbers}


def neonplug_document_from_resolved(
    codeplug: ResolvedCodeplug,
    *,
    analog_bandwidth_khz: float | None = None,
) -> dict[str, Any]:
    """Return the ``codeplug.json`` document dict for ``codeplug``.

    ``analog_bandwidth_khz`` is an explicit, caller-supplied fallback used
    only for channels that do not already carry their own
    ``bandwidth_khz`` -- it is never silently invented, and mirrors the same
    parameter on this repo's ``qdmr_yaml`` exporter.

    Raises ``ValueError`` with an actionable message for unsupported modes,
    missing/duplicate channel references, non-analog tone combinations, or
    codeplugs larger than the DM-32UV's NeonPlug-modeled capacity.
    """
    if len(codeplug.channels) > CHANNEL_MAX:
        raise ValueError(
            f"codeplug has {len(codeplug.channels)} channels, exceeds "
            f"NeonPlug's {CHANNEL_MAX}-channel limit for this radio"
        )
    if len(codeplug.zones) > ZONES_MAX:
        raise ValueError(
            f"codeplug has {len(codeplug.zones)} zones, exceeds NeonPlug's "
            f"{ZONES_MAX}-zone limit for this radio"
        )

    channel_numbers: dict[str, int] = {}
    channels_out: list[dict[str, Any]] = []
    for index, channel in enumerate(codeplug.channels, start=1):
        if channel.reference in channel_numbers:
            raise ValueError(
                f"duplicate channel reference {channel.reference!r} in codeplug.channels"
            )
        channel_numbers[channel.reference] = index
        channels_out.append(
            _channel_document(channel, index, analog_bandwidth_khz=analog_bandwidth_khz)
        )

    zones_out = [_zone_document(zone, channel_numbers) for zone in codeplug.zones]

    return {
        "version": FORMAT_VERSION,
        "channels": channels_out,
        "zones": zones_out,
    }


def write_neonplug(
    path: Path,
    codeplug: ResolvedCodeplug,
    *,
    analog_bandwidth_khz: float | None = None,
) -> None:
    """Write ``codeplug`` as a NeonPlug ``.neonplug`` archive at ``path``.

    The archive is a standard ZIP containing a single ``codeplug.json`` entry
    (``services/codeplugExport.ts`` ``CODEPLUG_JSON_FILENAME``), matching
    what NeonPlug itself writes and reads. Output is deterministic: the JSON
    is emitted with sorted keys and no wall-clock fields, and the ZIP entry's
    timestamp is pinned rather than using the time of generation. NeonPlug
    itself stamps an ``exportDate`` at import time when one is absent
    (``jsonSafeToCodeplug()``), so this exporter does not need to invent one.
    """
    document = neonplug_document_from_resolved(codeplug, analog_bandwidth_khz=analog_bandwidth_khz)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"

    path = Path(path)
    info = zipfile.ZipInfo(filename=CODEPLUG_JSON_FILENAME, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, payload)
