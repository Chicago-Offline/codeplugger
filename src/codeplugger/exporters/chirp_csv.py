"""Export resolved analog channels as CHIRP-compatible CSV."""

from __future__ import annotations

from csv import DictWriter
from io import StringIO
from pathlib import Path

from ..resolved import ResolvedChannel, ResolvedCodeplug


CHIRP_HEADERS = [
    "Location",
    "Name",
    "Frequency",
    "Duplex",
    "Offset",
    "Tone",
    "rToneFreq",
    "cToneFreq",
    "DtcsCode",
    "DtcsPolarity",
    "RxDtcsCode",
    "CrossMode",
    "Mode",
    "TStep",
    "Skip",
    "Power",
    "Comment",
]


def _format_frequency(value_mhz: float) -> str:
    return f"{value_mhz:.6f}"


def _tone_fields(channel: ResolvedChannel) -> dict[str, str]:
    tones = channel.tones
    has_ctcss = tones.ctcss_tx_hz is not None or tones.ctcss_rx_hz is not None
    has_dcs = tones.dcs_tx_code is not None or tones.dcs_rx_code is not None

    fields = {
        "Tone": "",
        "rToneFreq": "88.5",
        "cToneFreq": "88.5",
        "DtcsCode": "023",
        "DtcsPolarity": "NN",
        "RxDtcsCode": "023",
        "CrossMode": "Tone->Tone",
    }

    if not has_ctcss and not has_dcs:
        return fields

    if has_ctcss and has_dcs:
        raise ValueError(
            f"channel '{channel.display_name}' mixes CTCSS and DCS tones, "
            "which this CHIRP export path does not support"
        )

    if has_dcs:
        fields["Tone"] = "DTCS"
        tx_code = str(tones.dcs_tx_code or tones.dcs_rx_code)
        rx_code = str(tones.dcs_rx_code or tones.dcs_tx_code)
        fields["DtcsCode"] = tx_code
        fields["RxDtcsCode"] = rx_code
        return fields

    tx_tone = tones.ctcss_tx_hz
    rx_tone = tones.ctcss_rx_hz
    if tx_tone is not None:
        fields["rToneFreq"] = f"{tx_tone:.1f}"
    if rx_tone is not None:
        fields["cToneFreq"] = f"{rx_tone:.1f}"

    if tx_tone is not None and rx_tone is None:
        fields["Tone"] = "Tone"
        return fields

    fields["Tone"] = "TSQL"
    if tx_tone is None and rx_tone is not None:
        fields["rToneFreq"] = fields["cToneFreq"]
    return fields


def _duplex_and_offset(channel: ResolvedChannel) -> tuple[str, str]:
    if not channel.tx_permitted or channel.tx_frequency_mhz is None:
        return "off", _format_frequency(0.0)

    diff = channel.tx_frequency_mhz - channel.rx_frequency_mhz
    abs_diff = abs(diff)
    if abs_diff < 1e-6:
        return "", _format_frequency(0.0)

    # CHIRP uses "split" when TX cannot be represented as +/- offset.
    if abs_diff > 30.0:
        return "split", _format_frequency(channel.tx_frequency_mhz)

    duplex = "+" if diff > 0 else "-"
    return duplex, _format_frequency(abs_diff)


def chirp_csv_from_resolved(codeplug: ResolvedCodeplug) -> str:
    """Return CHIRP CSV text for resolved analog channels."""

    output = StringIO()
    writer = DictWriter(output, fieldnames=CHIRP_HEADERS, lineterminator="\n")
    writer.writeheader()

    for idx, channel in enumerate(codeplug.channels, start=1):
        mode = (channel.mode or "FM").upper()
        if mode != "FM":
            raise ValueError(
                f"channel '{channel.display_name}' mode '{mode}' is unsupported "
                "for CHIRP CSV export"
            )

        duplex, offset = _duplex_and_offset(channel)
        row = {
            "Location": str(idx),
            "Name": channel.display_name,
            "Frequency": _format_frequency(channel.rx_frequency_mhz),
            "Duplex": duplex,
            "Offset": offset,
            "Mode": "FM",
            "TStep": "5.00",
            "Skip": "",
            "Power": "High",
            "Comment": channel.notes or "",
        }
        row.update(_tone_fields(channel))
        writer.writerow(row)

    return output.getvalue()


def write_chirp_csv(path: Path, codeplug: ResolvedCodeplug) -> None:
    """Write CHIRP CSV to ``path``."""

    path.write_text(chirp_csv_from_resolved(codeplug), encoding="utf-8")
