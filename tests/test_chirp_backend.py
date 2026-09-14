from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeplugger.artifacts import ArtifactStore
from codeplugger.backends.chirp import ChirpTarget, ChirpWriter, ChirpWriterError


C64 = ChirpTarget(driver="Retevis_C64", model="Retevis C64")
FT277 = ChirpTarget(driver="Yaesu_VX-177", model="Yaesu VX-177", clone_mode="manual")


def _fake_run(
    calls: list[dict[str, object]],
    *,
    returncode: int = 0,
    document: dict[str, object] | None = None,
    stderr: str = "",
):
    """Stand in for the helper: record the call and write its result file."""

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append({"command": command, "kwargs": kwargs})
        if document is not None:
            result_path = Path(command[command.index("--result") + 1])
            result_path.write_text(json.dumps(document), encoding="utf-8")
        return type(
            "Result", (), {"returncode": returncode, "stdout": "", "stderr": stderr}
        )()

    return fake_run


def _command(calls: list[dict[str, object]], index: int = 0) -> list[str]:
    return calls[index]["command"]  # type: ignore[return-value]


def test_write_passes_the_model_gate_and_read_back_to_the_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeplugger.backends.chirp.subprocess.run",
        _fake_run(calls, document={"status": "ok", "channels_written": 34}),
    )

    document = ChirpWriter().write(
        tmp_path / "codeplug.csv",
        "/dev/cu.usbserial-110",
        target=C64,
        backup=tmp_path / "before.img",
        read_back=tmp_path / "after.img",
        confirm=True,
    )

    assert document["channels_written"] == 34
    command = _command(calls)
    assert command[:4] == ["chirp-writer", "write", "--port", "/dev/cu.usbserial-110"]
    assert "--expect-model" in command
    assert command[command.index("--expect-model") + 1] == "Retevis C64"
    assert command[command.index("--driver") + 1] == "Retevis_C64"
    assert str(tmp_path / "after.img") in command
    assert "--yes" in command
    # A radio that answers on demand must not stop and wait for a human.
    assert "--assume-ready" in command
    assert calls[0]["kwargs"]["capture_output"] is True


def test_write_refuses_without_confirm(tmp_path: Path) -> None:
    with pytest.raises(ChirpWriterError, match="confirm=True"):
        ChirpWriter().write(
            tmp_path / "codeplug.csv",
            "/dev/cu.usbserial-110",
            target=C64,
            backup=tmp_path / "before.img",
            read_back=tmp_path / "after.img",
        )


def test_write_refuses_to_skip_verification_silently(tmp_path: Path) -> None:
    with pytest.raises(ChirpWriterError, match="read_back path"):
        ChirpWriter().write(
            tmp_path / "codeplug.csv",
            "/dev/cu.usbserial-110",
            target=C64,
            backup=tmp_path / "before.img",
            confirm=True,
        )


def test_clone_mode_radio_keeps_the_terminal_for_its_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeplugger.backends.chirp.subprocess.run",
        _fake_run(calls, document={"status": "ok"}),
    )

    ChirpWriter().write(
        tmp_path / "codeplug.csv",
        "/dev/cu.usbserial-RTWBKOPI",
        target=FT277,
        backup=tmp_path / "before.img",
        read_back=tmp_path / "after.img",
        confirm=True,
    )

    assert "--assume-ready" not in _command(calls)
    assert calls[0]["kwargs"]["capture_output"] is False


def test_read_back_mismatch_raises_rather_than_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeplugger.backends.chirp.subprocess.run",
        _fake_run(
            calls,
            returncode=3,
            document={
                "status": "mismatch",
                "error": "1 read-back mismatches",
                "mismatches": [{"channel": 3, "field": "name"}],
            },
        ),
    )
    store = ArtifactStore(tmp_path / "profiles", "retevis_c64", "jhm_c64_01")

    with pytest.raises(ChirpWriterError, match="1 read-back mismatches"):
        ChirpWriter("chirp-writer", store).write(
            tmp_path / "codeplug.csv",
            "/dev/cu.usbserial-110",
            target=C64,
            backup=tmp_path / "before.img",
            read_back=tmp_path / "after.img",
            confirm=True,
        )

    log = store.log_path.read_text(encoding="utf-8")
    assert '"status": "mismatch"' in log


def test_device_operations_are_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "codeplugger.backends.chirp.subprocess.run",
        _fake_run(calls, document={"status": "ok", "model": "Retevis C64"}),
    )
    store = ArtifactStore(tmp_path / "profiles", "retevis_c64", "jhm_c64_01")

    assert ChirpWriter("chirp-writer", store).detect(
        "/dev/cu.usbserial-110", target=C64
    ) == "Retevis C64"

    log = store.log_path.read_text(encoding="utf-8")
    assert '"operation": "detect"' in log
    assert '"status": "success"' in log


def test_missing_helper_is_reported_as_a_backend_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def explode(*_: object, **__: object) -> object:
        raise FileNotFoundError("no chirp-writer")

    monkeypatch.setattr("codeplugger.backends.chirp.subprocess.run", explode)

    with pytest.raises(ChirpWriterError, match="could not run chirp-writer"):
        ChirpWriter().detect("/dev/cu.usbserial-110", target=C64)


def test_target_comes_from_capabilities() -> None:
    capabilities = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "radios"
            / "yaesu_ft277"
            / "capabilities.json"
        ).read_text(encoding="utf-8")
    )

    target = ChirpTarget.from_capabilities(capabilities)

    # The FT-277R reports as a VX-177: the name on the case is not the gate.
    assert (target.driver, target.model) == ("Yaesu_VX-177", "Yaesu VX-177")
    assert target.needs_operator


def test_radio_without_a_chirp_block_is_refused() -> None:
    with pytest.raises(ChirpWriterError, match="cannot be programmed through CHIRP"):
        ChirpTarget.from_capabilities({"id": "baofeng_dm32", "name": "Baofeng DM-32"})
