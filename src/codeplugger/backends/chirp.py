"""Invoke the GPL-licensed ``chirp-writer`` helper as a programming backend.

CHIRP is GPL-3 and codeplugger is Apache-2.0, so CHIRP is reached the same way
``dmrconf`` and ``p64tool`` are: as a separate process, never an import. The
helper lives in the ``chirp-writer`` repository; it drives CHIRP's own drivers
and ``import_logic`` and writes a JSON result file this wrapper reads.

CHIRP's own ``chirpc`` is not usable for this. It opens the port with
``serial.Serial(port=..., timeout=0.5)`` and never applies the driver's
``BAUD_RATE`` -- only ``chirp/wxui/clone.py`` does -- so radios that do not run
at 9600 never answer, and it has no CSV-import path at all.

Two facts shape the safety model here:

- The helper's mandatory pre-write backup doubles as the identity gate. It
  downloads before it uploads, and the driver validates the model bytes while
  parsing that image, so ``--expect-model`` aborts on the wrong radio before
  anything is written.
- Some radios must be put into clone mode by hand for every single transfer
  (the Yaesu ft7800 family). For those the helper has to talk to a human, so
  this wrapper runs it with the terminal attached instead of capturing its
  output, and reads the outcome from the result file rather than from stdout.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from ..artifacts import ArtifactStore

# The helper's exit code for "the upload happened but the read-back differs".
EXIT_MISMATCH = 3


class ChirpWriterError(RuntimeError):
    """Raised when chirp-writer cannot complete an operation."""


@dataclass(frozen=True)
class ChirpTarget:
    """Which CHIRP driver addresses a radio, and how it must be handled."""

    driver: str
    model: str
    clone_mode: str = "on_demand"

    @classmethod
    def from_capabilities(cls, capabilities: Mapping[str, Any]) -> "ChirpTarget":
        """Build a target from a radio's ``capabilities.json`` ``chirp`` block.

        A radio without that block is not addressable through CHIRP; refusing
        here is deliberate, because the alternative is guessing a driver and
        pushing an image at whatever answers.
        """

        block = capabilities.get("chirp")
        if not block:
            raise ChirpWriterError(
                f"radio '{capabilities.get('id', '?')}' has no chirp capabilities block; "
                "it cannot be programmed through CHIRP"
            )
        return cls(
            driver=block["driver"],
            model=block["model"],
            clone_mode=block.get("clone_mode", "on_demand"),
        )

    @property
    def needs_operator(self) -> bool:
        """True when a human must put the radio into clone mode per transfer."""

        return self.clone_mode == "manual"


@dataclass(frozen=True)
class ChirpWriter:
    """Safe command wrapper for a chirp-writer executable."""

    executable: Path | str = "chirp-writer"
    artifact_store: ArtifactStore | None = None

    def _run(
        self,
        operation: str,
        port: str,
        *,
        target: ChirpTarget,
        options: Sequence[str] = (),
        artifacts: Sequence[Path] = (),
        result: Path | None = None,
    ) -> dict[str, Any]:
        """Run one helper command and return its parsed result document."""

        with tempfile.TemporaryDirectory() as scratch:
            result_path = result if result is not None else Path(scratch) / "result.json"
            command = [
                str(self.executable),
                operation,
                "--port",
                port,
                "--driver",
                target.driver,
                "--expect-model",
                target.model,
                "--result",
                str(result_path),
                *options,
            ]
            if not target.needs_operator:
                command.append("--assume-ready")
            return self._execute(
                command,
                operation=operation,
                interactive=target.needs_operator,
                artifacts=artifacts,
                result_path=result_path,
            )

    def _execute(
        self,
        command: list[str],
        *,
        operation: str,
        interactive: bool,
        artifacts: Sequence[Path],
        result_path: Path,
    ) -> dict[str, Any]:
        try:
            # A clone-mode radio needs the operator to answer prompts, so the
            # helper keeps the terminal; its result file carries the outcome.
            completed = subprocess.run(
                command,
                check=False,
                capture_output=not interactive,
                text=True,
            )
        except OSError as exc:
            self._record(operation, "failure", command, artifacts, str(exc))
            raise ChirpWriterError(f"could not run chirp-writer: {exc}") from exc

        document: dict[str, Any] = {}
        if result_path.is_file():
            try:
                document = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self._record(operation, "failure", command, artifacts, str(exc))
                raise ChirpWriterError(
                    f"chirp-writer wrote an unreadable result file: {exc}"
                ) from exc

        if completed.returncode != 0:
            detail = document.get("error") or (
                (completed.stderr or completed.stdout or "").strip()
                if not interactive
                else ""
            )
            status = "mismatch" if completed.returncode == EXIT_MISMATCH else "failure"
            self._record(operation, status, command, artifacts, detail or None)
            raise ChirpWriterError(
                f"chirp-writer {operation} failed ({completed.returncode})"
                + (f": {detail}" if detail else "")
            )

        self._record(operation, "success", command, artifacts, None)
        return document

    def _record(
        self,
        operation: str,
        status: str,
        command: Sequence[str],
        artifacts: Sequence[Path],
        detail: str | None,
    ) -> None:
        if self.artifact_store is None:
            return
        self.artifact_store.record(
            operation, status, command=command, artifacts=artifacts, detail=detail
        )

    def detect(self, port: str, *, target: ChirpTarget) -> str:
        """Return the connected radio's identity from a read-only check.

        On a clone-mode radio this costs a full manual clone cycle, which is
        why :meth:`write` gates on its own backup download instead of calling
        this first.
        """

        document = self._run("detect", port, target=target)
        model = document.get("model")
        if not model:
            raise ChirpWriterError("chirp-writer detect reported no radio")
        return str(model)

    def read(self, port: str, output: Path, *, target: ChirpTarget) -> dict[str, Any]:
        """Save the radio's current image to ``output`` without writing to it."""

        return self._run(
            "read",
            port,
            target=target,
            options=("--out", str(output)),
            artifacts=(output,),
        )

    def write(
        self,
        csv: Path,
        port: str,
        *,
        target: ChirpTarget,
        backup: Path,
        read_back: Path | None = None,
        result: Path | None = None,
        confirm: bool = False,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Write a CHIRP CSV to a radio, requiring an explicit safety token.

        Gates, in order: ``confirm=True``; a read-back diff unless ``verify``
        is switched off; and, inside the helper, a mandatory pre-write backup
        whose model must match ``target.model``. The helper exits non-zero on
        any read-back mismatch, so a differing radio raises here rather than
        returning a document the caller has to remember to inspect.
        """

        if not confirm:
            raise ChirpWriterError("refusing to write without confirm=True")
        if verify and read_back is None:
            raise ChirpWriterError(
                "refusing to write without a read_back path; pass verify=False "
                "only when the radio cannot be read back"
            )

        options = ["--csv", str(csv), "--backup", str(backup)]
        if read_back is not None:
            options.extend(["--read-back", str(read_back)])
        options.append("--yes")
        artifacts = tuple(
            path for path in (csv, backup, read_back) if path is not None
        )
        return self._run(
            "write",
            port,
            target=target,
            options=options,
            artifacts=artifacts,
            result=result,
        )
