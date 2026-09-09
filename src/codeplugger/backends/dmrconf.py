"""Invoke qdmr's GPL-licensed ``dmrconf`` CLI as a programming backend.

Semantics verified against qdmr @ ``e84d4b3a`` (dmrconf 0.15.1):

- ``detect`` prints ``Found: <radio name>`` for the connected radio
  (``cli/detect.cc``).
- ``write`` runs the radio's limit checks but only *logs* critical issues;
  its ``verified`` flag is never cleared, so it uploads anyway
  (``cli/writecodeplug.cc``). The explicit ``verify`` gate in
  :meth:`Dmrconf.write` is therefore load-bearing, not belt-and-braces.
- ``write`` auto-detects the radio model; this wrapper never passes
  ``--radio`` to a device write, so a mismatched codeplug cannot be forced
  onto a misdetected radio. Instead the detected name must equal the
  expected name for the target radio key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Sequence

from ..artifacts import ArtifactStore

# dmrconf radio keys mapped to the device names `detect` reports
# (`dmrconf --list-radios`).
RADIO_NAMES = {
    "dm32uv": "DM-32UV",
}


class DmrconfError(RuntimeError):
    """Raised when dmrconf cannot complete an operation."""


def _format_args(path: Path) -> list[str]:
    """Force the file format explicitly instead of trusting extensions."""

    if path.suffix in (".yaml", ".yml"):
        return ["--yaml"]
    if path.suffix in (".conf", ".csv"):
        return ["--csv"]
    return ["--bin"]


@dataclass(frozen=True)
class Dmrconf:
    """Safe command wrapper for a dmrconf executable."""

    executable: Path | str = "dmrconf"
    artifact_store: ArtifactStore | None = None

    def _run(self, args: Sequence[str], *, artifacts: Sequence[Path] = ()) -> str:
        command = [str(self.executable), *args]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            if self.artifact_store is not None:
                self.artifact_store.record(
                    args[0], "failure", command=command, artifacts=artifacts, detail=str(exc)
                )
            raise DmrconfError(f"could not run dmrconf: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if self.artifact_store is not None:
                self.artifact_store.record(
                    args[0], "failure", command=command, artifacts=artifacts, detail=detail
                )
            raise DmrconfError(
                f"dmrconf {' '.join(args)} failed ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        if self.artifact_store is not None:
            self.artifact_store.record(
                args[0], "success", command=command, artifacts=artifacts
            )
        return result.stdout

    def detect(self) -> str:
        """Return the detected radio name from a read-only liveness check."""

        output = self._run(["detect"])
        for line in output.splitlines():
            if line.startswith("Found: "):
                return line.removeprefix("Found: ").strip()
        raise DmrconfError(f"dmrconf detect reported no radio: {output.strip()}")

    def verify(self, codeplug: Path, *, radio_key: str) -> str:
        """Verify a codeplug file against a radio's limits without hardware."""

        args = [
            "verify",
            *_format_args(codeplug),
            f"--radio={radio_key}",
            str(codeplug),
        ]
        return self._run(args, artifacts=(codeplug,))

    def read(self, output: Path) -> str:
        """Read the connected radio's codeplug into ``output``."""

        args = ["read", *_format_args(output), str(output)]
        return self._run(args, artifacts=(output,))

    def write(
        self,
        codeplug: Path,
        *,
        radio_key: str,
        expected_name: str | None = None,
        confirm: bool = False,
        verify_first: bool = True,
        read_back: Path | None = None,
        init_codeplug: bool = False,
    ) -> str:
        """Write a codeplug, requiring an explicit caller safety token.

        Gates, in order: ``confirm=True``; a headless ``verify`` against
        ``radio_key`` (dmrconf's own write-time check does not abort, see
        module docstring); ``detect`` must report the expected device name.
        ``read_back`` optionally archives the post-write device state for
        the operation log.
        """

        if not confirm:
            raise DmrconfError("refusing to write without confirm=True")
        if expected_name is None:
            expected_name = RADIO_NAMES.get(radio_key)
        if expected_name is None:
            raise DmrconfError(
                f"no known device name for radio key '{radio_key}'; "
                "pass expected_name explicitly"
            )
        if verify_first:
            self.verify(codeplug, radio_key=radio_key)
        detected = self.detect()
        if detected != expected_name:
            raise DmrconfError(
                f"connected radio is '{detected}', expected '{expected_name}'; "
                "refusing to write"
            )
        args = ["write", *_format_args(codeplug)]
        if init_codeplug:
            args.append("--init-codeplug")
        args.append(str(codeplug))
        output = self._run(args, artifacts=(codeplug,))
        if read_back is not None:
            self.read(read_back)
        return output
