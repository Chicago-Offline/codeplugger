from datetime import datetime, timezone
from pathlib import Path

from codeplugger.artifacts import (
    ArtifactStore,
    html_reference_from_resolved,
    markdown_reference_from_resolved,
    write_profile_artifacts,
)
from codeplugger.resolved import (
    ResolvedChannel,
    ResolvedCodeplug,
    ResolvedTones,
    ResolvedZone,
)


def test_artifact_store_appends_per_radio_operation_log(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "profiles", "test_radio", "radio_01")

    store.record("write", "success", command=("radio-tool", "write"))
    store.record("read", "failure", detail="cable disconnected")

    lines = store.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"operation": "write"' in lines[0]
    assert '"radio_instance_id": "radio_01"' in lines[0]
    assert '"detail": "cable disconnected"' in lines[1]


def test_html_reference_contains_printable_zone_and_channel_details() -> None:
    codeplug = ResolvedCodeplug(
        "test_radio",
        "radio_01",
        {"dmr_id": 682041, "tape_color": "blue"},
        (
            ResolvedChannel(
                "one", "one", "FAMILY", 446.0, None, "FM", "gmrs",
                ResolvedTones(), False, "listen only"
            ),
        ),
        (ResolvedZone("family", "Family", ("one",)),),
    )

    rendered = html_reference_from_resolved(
        codeplug,
        radio_name="Retevis MateTalk P4",
        fleet_instances={
            "radio_01": {"dmr_id": 682041, "dmr_contact_name": "COP4BLUE"},
            "radio_02": {"dmr_id": 682042, "dmr_contact_name": "COP4YELLOW"},
        },
        programmed_at=datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc),
    )

    assert "@media print" in rendered
    assert "<h1>Chicago Offline - MateTalk P4 - 682041 - Blue</h1>" in rendered
    assert "<h3>Programmed: 2026.09.03 05:00 UTC</h3>" in rendered
    assert "<h3>Channels: 1 | Zones: 1 | Contacts: 2</h3>" in rendered
    assert "<h2>Zones / Channels</h2>" in rendered
    assert "<h3>Zone 1 - Family</h3>" in rendered
    assert "FAMILY" in rendered
    assert "RX only" in rendered
    assert "listen only" in rendered
    assert "<h2>Contacts</h2>" in rendered
    assert "COP4BLUE" in rendered
    assert "COP4YELLOW" in rendered


def test_profile_artifacts_are_shared_across_radio_models(tmp_path: Path) -> None:
    codeplug = ResolvedCodeplug("baofeng_dm32", "dm32_01", None, (), ())

    reference_path, log_path = write_profile_artifacts(tmp_path / "profiles", codeplug)

    assert reference_path == tmp_path / "profiles" / ".artifacts" / "baofeng_dm32" / "dm32_01" / "reference.html"
    assert reference_path.exists()
    assert '"operation": "generate"' in log_path.read_text(encoding="utf-8")

def test_markdown_reference_mirrors_html_structure_as_tables() -> None:
    codeplug = ResolvedCodeplug(
        "test_radio",
        "radio_01",
        {"dmr_id": 682041, "tape_color": "blue"},
        (
            ResolvedChannel(
                "one", "one", "FAMILY", 446.0, None, "FM", "gmrs",
                ResolvedTones(), False, "listen only"
            ),
            ResolvedChannel(
                "two", "two", "FAM | PIPE", 462.5625, 467.5625, "DMR", "gmrs",
                ResolvedTones(), True, None
            ),
        ),
        (ResolvedZone("family", "Family", ("one", "two")),),
    )

    rendered = markdown_reference_from_resolved(
        codeplug,
        radio_name="Retevis MateTalk P4",
        fleet_instances={
            "radio_01": {"dmr_id": 682041, "dmr_contact_name": "COP4BLUE"},
            "radio_02": {"dmr_id": 682042, "dmr_contact_name": "COP4YELLOW"},
        },
        programmed_at=datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc),
    )

    assert rendered.startswith("# Chicago Offline - MateTalk P4 - 682041 - Blue\n")
    assert "Programmed: 2026.09.03 05:00 UTC" in rendered
    assert "Channels: 2 | Zones: 1 | Contacts: 2" in rendered
    assert "## Zones / Channels" in rendered
    assert "### Zone 1 - Family" in rendered
    assert (
        "| # | Name | RX | TX | Mode | BW kHz | Power W | Tone RX | Tone TX | "
        "CC | TS | Service | TX permitted | Notes |"
    ) in rendered
    assert (
        "| 1 | FAMILY | 446.000000 | RX only | FM |  |  |  |  |  |  | gmrs | "
        "No | listen only |"
    ) in rendered
    assert (
        "| 2 | FAM \\| PIPE | 462.562500 | 467.562500 | DMR |  |  |  |  |  |  | "
        "gmrs | Yes |  |"
    ) in rendered
    assert "| 1 | COP4BLUE | 682041 | Private |" in rendered
    assert "<td>" not in rendered
    assert rendered.endswith("\n")


def test_write_profile_artifacts_writes_both_reference_formats(tmp_path: Path) -> None:
    codeplug = ResolvedCodeplug(
        "test_radio",
        "radio_01",
        {"dmr_id": 682041},
        (
            ResolvedChannel(
                "one", "one", "FAMILY", 446.0, None, "FM", "gmrs",
                ResolvedTones(), False, None
            ),
        ),
        (ResolvedZone("family", "Family", ("one",)),),
    )

    reference_path, log_path = write_profile_artifacts(tmp_path, codeplug)
    markdown_path = reference_path.with_name("reference.md")

    assert reference_path.name == "reference.html"
    assert markdown_path.exists()
    assert markdown_path.read_text(encoding="utf-8").startswith("# ")
    assert "reference.md" in log_path.read_text(encoding="utf-8")
