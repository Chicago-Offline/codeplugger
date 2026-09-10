"""Derive channel bandwidth from ITU emission designators.

SSRF-Lite records ``tx.emission`` (an ITU designator such as ``16K0F3E``) as a
reference-only fact and treats ``tx.bandwidth_khz`` as optional. Large parts of
the public data set carry only the designator, but every radio exporter needs a
concrete channel bandwidth. This module turns the designator into the channel
bandwidth a programmer would actually select in a CPS.

The first field of a designator is the *necessary bandwidth* of the emission,
not the channel spacing the radio is programmed to. ``16K0F3E`` is 16.0 kHz of
occupied bandwidth and is programmed as a 25 kHz ("wide") channel; ``11K2F3E``
is 11.2 kHz and is programmed as 12.5 kHz ("narrow"). Because that mapping is
a convention rather than arithmetic, known designators are resolved from an
explicit table and anything unrecognised returns ``None`` so callers keep
raising their existing "no bandwidth" error instead of inventing one.
"""

from __future__ import annotations

import re

__all__ = [
    "necessary_bandwidth_khz",
    "bandwidth_khz_from_emission",
]

# Designator -> channel bandwidth in kHz. Every entry here is grounded in
# SSRF-Lite data that already pairs the designator with an explicit
# bandwidth_khz, so derivation reproduces the curated value rather than
# guessing a new one.
_KNOWN_BANDWIDTHS_KHZ: dict[str, float] = {
    "6K00A3E": 25.0,   # AM aviation voice, 25 kHz airband spacing
    "6K00F7W": 6.25,   # NXDN 6.25 kHz
    "8K50F7W": 12.5,   # NXDN / digital voice on 12.5 kHz
    "7K60FXE": 12.5,   # DMR two-slot TDMA in a 12.5 kHz channel
    "11K2F3E": 12.5,   # narrowband FM voice
    "16K0F3E": 25.0,   # wideband FM voice
    "20K0F3E": 25.0,   # wideband FM voice
}

# ``7K60FXE`` appears in curated data with bandwidth_khz: 25 in a handful of
# places, but DMR is a 12.5 kHz channel and qdmr emits DMR channels without a
# bandwidth field at all, so the value is inert for the DMR path. 12.5 is used
# here as the physically correct figure.

# Multiplier for the unit letter used in place of the decimal point.
_UNIT_TO_KHZ: dict[str, float] = {
    "H": 0.001,
    "K": 1.0,
    "M": 1000.0,
    "G": 1000000.0,
}

# The bandwidth field is always exactly four characters, with the unit letter
# standing in for the decimal point wherever it falls:
#   "16K0F3E" -> ("16", "K", "0")
#   "6K00A3E" -> ("6", "K", "00")
#   "300HA1A" -> ("300", "H", "")
_NECESSARY_BANDWIDTH = re.compile(r"^(\d{1,3})([HKMG])(\d{0,2})$")


def necessary_bandwidth_khz(emission: str | None) -> float | None:
    """Return the necessary bandwidth in kHz encoded in ``emission``.

    The unit letter stands in for the decimal point, so ``16K0`` is 16.0 kHz
    and ``6K00`` is 6.00 kHz. Designators without a bandwidth field (such as
    the bare ``A1A``) return ``None``.
    """

    if not emission:
        return None
    match = _NECESSARY_BANDWIDTH.match(emission.strip().upper()[:4])
    if match is None:
        return None
    whole, unit, fraction = match.groups()
    value = float(f"{whole}.{fraction or 0}")
    return value * _UNIT_TO_KHZ[unit]


def bandwidth_khz_from_emission(emission: str | None) -> float | None:
    """Return the channel bandwidth in kHz implied by ``emission``.

    Returns ``None`` when the designator is missing, malformed, or of a class
    whose channel bandwidth cannot be established from the designator alone.
    Callers must treat ``None`` as "unknown" and surface their own error.
    """

    if not emission:
        return None
    designator = emission.strip().upper()
    known = _KNOWN_BANDWIDTHS_KHZ.get(designator)
    if known is not None:
        return known

    necessary = necessary_bandwidth_khz(designator)
    if necessary is None:
        return None

    # Analogue FM voice (F3E) is the only class common enough, and regular
    # enough, to extrapolate: pick the narrowest standard step that contains
    # the occupied bandwidth. Digital and data classes vary by protocol and are
    # left unknown on purpose.
    if designator.endswith("F3E"):
        for step in (12.5, 25.0):
            if necessary <= step:
                return step
        return None

    return None
