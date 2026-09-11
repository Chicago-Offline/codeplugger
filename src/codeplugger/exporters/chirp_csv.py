"""Export resolved analog channels as CHIRP-compatible CSV.

Two constraints shape this exporter:

``Duplex`` is limited to ``+``, ``-`` or empty. CHIRP's in-memory model also
allows ``split`` and ``off``, but its CSV *parser* does not
(``chirp_common.really_from_csv``, verified against ``kk7ds/chirp`` @
``a229fae``), so emitting either produces a file CHIRP cannot import.

Receive-only channels are exported as ordinary simplex channels. That mirrors
existing practice in our own reference codeplugs: in
``muehlstein-codeplugger-profiles`` the CPD/CFD receive-only blocks
(``BF-888_CPDCFD.img`` ch11-16, ``TYT_TH-9800``) are stored with ``tx == rx``
and no transmit inhibit, with the intent carried in the channel name. Callers
that need transmit actually blocked must enforce it outside the CSV.
"""

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
        # CHIRP writes DTCS codes zero-padded to three digits ("%03i"), and
        # existing reference exports use that form. Match it so generated CSVs
        # are byte-comparable with CPS/CHIRP output.
        tx_code = tones.dcs_tx_code if tones.dcs_tx_code is not None else tones.dcs_rx_code
        rx_code = tones.dcs_rx_code if tones.dcs_rx_code is not None else tones.dcs_tx_code
        fields["DtcsCode"] = f"{int(tx_code):03d}"
        fields["RxDtcsCode"] = f"{int(rx_code):03d}"
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
    """Return CHIRP ``(Duplex, Offset)`` for one channel.

    CHIRP's CSV reader (``chirp_common.really_from_csv``) accepts only ``+``,
    ``-`` or an empty ``Duplex``; ``split`` and ``off`` raise
    ``InvalidDataError`` and make the whole row unimportable. Every value
    returned here is therefore one of those three.

    Receive-only channels are emitted as plain simplex, matching how they are
    actually stored in our reference codeplugs (see module docstring). The
    receive-only intent is carried by the channel name/comment, not by the
    frequency fields.
    """

    if not channel.tx_permitted or channel.tx_frequency_mhz is None:
        return "", _format_frequency(0.0)

    diff = channel.tx_frequency_mhz - channel.rx_frequency_mhz
    abs_diff = abs(diff)
    if abs_diff < 1e-6:
        return "", _format_frequency(0.0)

    duplex = "+" if diff > 0 else "-"
    return duplex, _format_frequency(abs_diff)


def chirp_csv_from_resolved(codeplug: ResolvedCodeplug) -> str:
    """Return CHIRP CSV text for resolved analog channels."""

    output = StringIO()
    writer = DictWriter(output, fieldnames=CHIRP_HEADERS, lineterminator="\n")
    writer.writeheader()

    for idx, channel in enumerate(codeplug.channels, start=1):
        raw_mode = (channel.mode or "FM").upper()
        if raw_mode not in ("FM", "AM"):
            raise ValueError(
                f"channel '{channel.display_name}' mode '{raw_mode}' is unsupported "
                "for CHIRP CSV export"
            )
        chirp_mode = raw_mode  # CHIRP CSV accepts FM and AM directly

        duplex, offset = _duplex_and_offset(channel)
        row = {
            "Location": str(idx),
            "Name": channel.display_name,
            "Frequency": _format_frequency(channel.rx_frequency_mhz),
            "Duplex": duplex,
            "Offset": offset,
            "Mode": chirp_mode,
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
