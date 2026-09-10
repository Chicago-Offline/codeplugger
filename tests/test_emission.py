from __future__ import annotations

import pytest

from codeplugger.emission import (
    bandwidth_khz_from_emission,
    necessary_bandwidth_khz,
)


@pytest.mark.parametrize(
    ("emission", "expected"),
    [
        ("16K0F3E", 16.0),
        ("11K2F3E", 11.2),
        ("6K00A3E", 6.0),
        ("20K0F3E", 20.0),
        ("7K60FXE", 7.6),
        ("8K50F7W", 8.5),
        ("300H A1A".replace(" ", ""), 0.3),
    ],
)
def test_necessary_bandwidth_parses_unit_letter_as_decimal_point(
    emission: str, expected: float
) -> None:
    assert necessary_bandwidth_khz(emission) == pytest.approx(expected)


@pytest.mark.parametrize("emission", [None, "", "A1A", "F3E", "not-a-designator"])
def test_necessary_bandwidth_returns_none_without_a_bandwidth_field(
    emission: str | None,
) -> None:
    assert necessary_bandwidth_khz(emission) is None


@pytest.mark.parametrize(
    ("emission", "expected"),
    [
        # Every pairing below matches SSRF-Lite entries that already carry an
        # explicit bandwidth_khz alongside the designator.
        ("16K0F3E", 25.0),
        ("20K0F3E", 25.0),
        ("11K2F3E", 12.5),
        ("6K00A3E", 25.0),
        ("8K50F7W", 12.5),
        ("6K00F7W", 6.25),
    ],
)
def test_known_designators_reproduce_curated_bandwidths(
    emission: str, expected: float
) -> None:
    assert bandwidth_khz_from_emission(emission) == expected


def test_designator_is_case_and_whitespace_insensitive() -> None:
    assert bandwidth_khz_from_emission("  16k0f3e ") == 25.0


@pytest.mark.parametrize(
    ("emission", "expected"),
    [
        # Unlisted analogue FM voice extrapolates to the narrowest standard
        # step that contains the occupied bandwidth.
        ("11K0F3E", 12.5),
        ("7K60F3E", 12.5),
        ("13K0F3E", 25.0),
    ],
)
def test_unlisted_fm_voice_rounds_up_to_a_standard_step(
    emission: str, expected: float
) -> None:
    assert bandwidth_khz_from_emission(emission) == expected


@pytest.mark.parametrize(
    "emission",
    [
        None,
        "",
        "A1A",           # no bandwidth field at all
        "16K0F2D",       # data class; channel bandwidth is protocol-specific
        "8K10F1E",       # digital voice; not derivable from the designator
        "4K00F1E",
        "30K0F3E",       # wider than any standard step
        "garbage",
    ],
)
def test_undecidable_designators_return_none(emission: str | None) -> None:
    assert bandwidth_khz_from_emission(emission) is None
