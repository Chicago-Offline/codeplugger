from __future__ import annotations

from pathlib import Path

import pytest

from codeplugger.backends.p64tool import P64Tool, P64ToolError
from codeplugger.artifacts import ArtifactStore


def test_p64tool_write_backs_up_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()

    monkeypatch.setattr("codeplugger.backends.p64tool.subprocess.run", fake_run)
    backup = tmp_path / "pre_write.bin"

    output = P64Tool("p64tool").write(
        "/dev/cu.p4", backup=backup, config=Path("radio.toml"), confirm=True
    )

    assert output == "ok\n"
    assert calls == [
        ["p64tool", "read", "--port", "/dev/cu.p4", "--out", str(backup)],
        [
            "p64tool",
            "write",
            "--port",
            "/dev/cu.p4",
            "radio.toml",
            "--require-known-version",
            "--yes",
        ],
    ]


def test_p64tool_refuses_to_overwrite_an_existing_backup(tmp_path: Path) -> None:
    backup = tmp_path / "pre_write.bin"
    backup.write_bytes(b"earlier session")

    with pytest.raises(P64ToolError, match="already exists"):
        P64Tool().write("/dev/cu.p4", backup=backup, confirm=True)


def test_p64tool_refuses_unconfirmed_write(tmp_path: Path) -> None:
    with pytest.raises(P64ToolError, match="confirm=True"):
        P64Tool().write("/dev/cu.p4", backup=tmp_path / "pre_write.bin")


def test_p64tool_reports_command_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_: list[str], **__: object) -> object:
        return type("Result", (), {"returncode": 2, "stdout": "", "stderr": "bad cable\n"})()

    monkeypatch.setattr("codeplugger.backends.p64tool.subprocess.run", fake_run)

    with pytest.raises(P64ToolError, match="bad cable"):
        P64Tool().info("/dev/cu.p4")


def test_p64tool_can_audit_device_operations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(command: list[str], **_: object) -> object:
        return type("Result", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()

    monkeypatch.setattr("codeplugger.backends.p64tool.subprocess.run", fake_run)
    store = ArtifactStore(tmp_path / "profiles", "retevis_matetalk_p4", "p4_01")

    P64Tool("p64tool", store).write(
        "/dev/cu.p4",
        backup=tmp_path / "pre_write.bin",
        config=Path("radio.toml"),
        confirm=True,
    )

    log = store.log_path.read_text(encoding="utf-8")
    assert '"operation": "write"' in log
    assert '"status": "success"' in log
    assert 'radio.toml' in log