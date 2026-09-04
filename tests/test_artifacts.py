from pathlib import Path

from codeplugger.artifacts import (
    ArtifactStore,
    html_reference_from_resolved,
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
        None,
        (
            ResolvedChannel(
                "one", "one", "FAMILY", 446.0, None, "FM", "gmrs",
                ResolvedTones(), False, "listen only"
            ),
        ),
        (ResolvedZone("family", "Family", ("one",)),),
    )

    rendered = html_reference_from_resolved(codeplug)

    assert "@media print" in rendered
    assert "Family" in rendered
    assert "FAMILY" in rendered
    assert "RX only" in rendered
    assert "listen only" in rendered


def test_profile_artifacts_are_shared_across_radio_models(tmp_path: Path) -> None:
    codeplug = ResolvedCodeplug("baofeng_dm32", "dm32_01", None, (), ())

    reference_path, log_path = write_profile_artifacts(tmp_path / "profiles", codeplug)

    assert reference_path == tmp_path / "profiles" / ".artifacts" / "baofeng_dm32" / "dm32_01" / "reference.html"
    assert reference_path.exists()
    assert '"operation": "generate"' in log_path.read_text(encoding="utf-8")