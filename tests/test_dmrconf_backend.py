from __future__ import annotations

from pathlib import Path

import pytest

from codeplugger.artifacts import ArtifactStore
from codeplugger.backends.dmrconf import Dmrconf, DmrconfError


def _fake_run(calls: list[list[str]], stdout_by_command: dict[str, str]):
    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        stdout = stdout_by_command.get(command[1], "")
        return type(
            "Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""}
        )()

    return fake_run


def test_write_verifies_detects_then_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run",
        _fake_run(calls, {"detect": "Found: DM-32UV\n", "write": "done\n"}),
    )

    output = Dmrconf().write(
        Path("codeplug.yaml"), radio_key="dm32uv", confirm=True
    )

    assert output == "done\n"
    assert calls == [
        ["dmrconf", "verify", "--yaml", "--radio=dm32uv", "codeplug.yaml"],
        ["dmrconf", "detect"],
        ["dmrconf", "write", "--yaml", "codeplug.yaml"],
    ]


def test_write_refuses_without_confirm() -> None:
    with pytest.raises(DmrconfError, match="confirm=True"):
        Dmrconf().write(Path("codeplug.yaml"), radio_key="dm32uv")


def test_write_refuses_mismatched_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run",
        _fake_run(calls, {"detect": "Found: OpenGD77\n"}),
    )

    with pytest.raises(DmrconfError, match="refusing to write"):
        Dmrconf().write(
            Path("codeplug.yaml"), radio_key="dm32uv", confirm=True
        )

    assert ["dmrconf", "write", "--yaml", "codeplug.yaml"] not in calls


def test_write_refuses_unknown_radio_key() -> None:
    with pytest.raises(DmrconfError, match="no known device name"):
        Dmrconf().write(
            Path("codeplug.yaml"), radio_key="unknownradio", confirm=True
        )


def test_write_can_archive_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run",
        _fake_run(calls, {"detect": "Found: DM-32UV\n"}),
    )

    Dmrconf().write(
        Path("codeplug.yaml"),
        radio_key="dm32uv",
        confirm=True,
        read_back=Path("post_write.yaml"),
    )

    assert calls[-1] == ["dmrconf", "read", "--yaml", "post_write.yaml"]


def test_verify_failure_blocks_write(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        if command[1] == "verify":
            return type(
                "Result",
                (),
                {"returncode": 255, "stdout": "", "stderr": "ERROR: too many\n"},
            )()
        return type(
            "Result", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()

    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run", fake_run
    )

    with pytest.raises(DmrconfError, match="too many"):
        Dmrconf().write(
            Path("codeplug.yaml"), radio_key="dm32uv", confirm=True
        )

    assert len(calls) == 1


def test_detect_requires_found_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run",
        _fake_run([], {"detect": "no radios here\n"}),
    )

    with pytest.raises(DmrconfError, match="no radio"):
        Dmrconf().detect()


def test_operations_are_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "codeplugger.backends.dmrconf.subprocess.run",
        _fake_run([], {"detect": "Found: DM-32UV\n"}),
    )
    store = ArtifactStore(tmp_path / "profiles", "baofeng_dm32", "dm32_01")

    Dmrconf(artifact_store=store).write(
        tmp_path / "codeplug.yaml", radio_key="dm32uv", confirm=True
    )

    log = store.log_path.read_text(encoding="utf-8")
    assert '"operation": "verify"' in log
    assert '"operation": "detect"' in log
    assert '"operation": "write"' in log
    assert '"status": "success"' in log
