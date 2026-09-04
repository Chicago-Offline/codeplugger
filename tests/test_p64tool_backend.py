from __future__ import annotations

from pathlib import Path

import pytest

from codeplugger.backends.p64tool import P64Tool, P64ToolError


def test_p64tool_write_is_explicit_and_verified_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()

    monkeypatch.setattr("codeplugger.backends.p64tool.subprocess.run", fake_run)

    output = P64Tool("p64tool").write(
        "/dev/cu.p4", config=Path("radio.toml"), confirm=True
    )

    assert output == "ok\n"
    assert calls == [[
        "p64tool",
        "write",
        "--port",
        "/dev/cu.p4",
        "radio.toml",
        "--require-known-version",
        "--yes",
    ]]


def test_p64tool_refuses_unconfirmed_write() -> None:
    with pytest.raises(P64ToolError, match="confirm=True"):
        P64Tool().write("/dev/cu.p4")


def test_p64tool_reports_command_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_: list[str], **__: object) -> object:
        return type("Result", (), {"returncode": 2, "stdout": "", "stderr": "bad cable\n"})()

    monkeypatch.setattr("codeplugger.backends.p64tool.subprocess.run", fake_run)

    with pytest.raises(P64ToolError, match="bad cable"):
        P64Tool().info("/dev/cu.p4")