"""Invoke the external MIT-licensed p64tool P4 programmer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Sequence


class P64ToolError(RuntimeError):
    """Raised when p64tool cannot complete an operation."""


@dataclass(frozen=True)
class P64Tool:
    """Safe command wrapper for a p64tool executable."""

    executable: Path | str = "p64tool"

    def _run(self, args: Sequence[str]) -> str:
        command = [str(self.executable), *args]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise P64ToolError(f"could not run p64tool: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise P64ToolError(
                f"p64tool {' '.join(args)} failed ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        return result.stdout

    def info(self, port: str) -> str:
        """Perform a read-only device liveness and identity check."""

        return self._run(["info", "--port", port])

    def read(self, port: str, output: Path, *, verbose: bool = False) -> str:
        """Read a complete raw backup from a P4 without writing it."""

        args = ["read", "--port", port, "--out", str(output)]
        if verbose:
            args.append("--verbose")
        return self._run(args)

    def roundtrip(self, dump: Path) -> str:
        """Verify that a dump decodes and re-encodes byte-for-byte."""

        return self._run(["roundtrip", str(dump)])

    def write(
        self,
        port: str,
        *,
        config: Path | None = None,
        from_dump: Path | None = None,
        confirm: bool = False,
        require_known_version: bool = True,
        verify: bool = True,
        verbose: bool = False,
    ) -> str:
        """Write a P4 config, requiring an explicit caller safety token.

        The wrapper requires ``confirm=True`` before passing p64tool's
        ``--yes`` safety flag to the subprocess.
        """

        if not confirm:
            raise P64ToolError("refusing to write without confirm=True")
        args = ["write", "--port", port]
        if config is not None:
            args.append(str(config))
        if from_dump is not None:
            args.extend(["--from-dump", str(from_dump)])
        if require_known_version:
            args.append("--require-known-version")
        if not verify:
            args.append("--no-verify")
        if verbose:
            args.append("--verbose")
        args.append("--yes")
        return self._run(args)