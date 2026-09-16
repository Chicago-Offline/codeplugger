"""Invoke the external benlink ``apply_codeplug.py`` writer for Benshi radios.

Benshi-protocol handhelds (Vero VR-N76 and relatives) have no desktop CPS and
no CHIRP driver. The only programming path is over the air, so this backend
drives ``scripts/apply_codeplug.py`` from the benlink fork with a
``benlink-plan`` artifact produced by ``exporters.benlink_plan``.

**Why this backend can run over SSH.** Every other backend here talks to a
cable on the same machine. This one usually cannot: RFCOMM needs
``socket.AF_BLUETOOTH``, which CPython does not provide on macOS, so the
writer has to run on a Linux host that is physically near the radio -- in
practice a Pi. Setting ``ssh_host`` copies the plan there and runs the writer
remotely, which keeps the generator and the Bluetooth adapter on different
machines without anyone hand-copying JSON.

The BLE transport does work on macOS, but only with a bond created by a
foreground GUI process, which an automated run is not. Prefer RFCOMM on
Linux and treat BLE as the interactive fallback.

Safety model, all of it enforced by the writer rather than here, and all of
it the reason this backend does not just shell out to the controller:

* the plan's ``vendor_id``/``product_id`` are checked against the connected
  radio before any write, because every Benshi HT speaks this protocol
* ``apply`` backs up every region first, not only the ones it writes
* every written slot is read back and diffed, since a declined write is
  indistinguishable from an accepted one on the wire
* regions the plan does not name are never touched

``apply`` here additionally refuses to run without ``confirm=True``, matching
the p64tool wrapper: the caller has to say out loud that it means to write to
a radio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
import subprocess
from typing import Sequence

from ..artifacts import ArtifactStore


class BenlinkError(RuntimeError):
    """Raised when the benlink writer cannot complete an operation."""


@dataclass(frozen=True)
class Benlink:
    """Safe command wrapper for benlink's ``apply_codeplug.py``.

    ``python_executable`` and ``script`` are resolved on whichever host the
    writer runs on, so for a remote run they are paths on ``ssh_host``, not
    local ones. A benlink checkout normally keeps its own virtualenv, which
    is why the interpreter is configurable rather than assumed to be the one
    running codeplugger.
    """

    script: Path | str = "scripts/apply_codeplug.py"
    python_executable: Path | str = "python3"
    ssh_host: str | None = None
    ssh_options: tuple[str, ...] = ()
    remote_plan_dir: str = "/tmp"
    artifact_store: ArtifactStore | None = None
    timeout_seconds: float | None = 600.0

    # -- command construction ------------------------------------------------
    def _writer_command(self, args: Sequence[str]) -> list[str]:
        return [str(self.python_executable), str(self.script), *args]

    def _command(self, args: Sequence[str]) -> list[str]:
        writer = self._writer_command(args)
        if self.ssh_host is None:
            return writer
        # Quote for the remote shell: an SSH command line is re-parsed by the
        # login shell on the far side, so a path with a space would otherwise
        # split into two arguments.
        remote = " ".join(shlex.quote(part) for part in writer)
        return ["ssh", *self.ssh_options, self.ssh_host, remote]

    def _run(self, args: Sequence[str], *, artifacts: Sequence[Path] = ()) -> str:
        command = self._command(args)
        operation = args[0].lstrip("-") if args else "benlink"
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if self.artifact_store is not None:
                self.artifact_store.record(
                    operation, "failure", command=command,
                    artifacts=artifacts, detail=str(exc),
                )
            raise BenlinkError(f"could not run benlink writer: {exc}") from exc
        if result.returncode != 0:
            # The writer prints its diff to stdout and only fatal errors to
            # stderr, so a verify mismatch is most of the useful detail.
            detail = (result.stdout or result.stderr).strip()
            if self.artifact_store is not None:
                self.artifact_store.record(
                    operation, "failure", command=command,
                    artifacts=artifacts, detail=detail,
                )
            raise BenlinkError(
                f"benlink {operation} failed ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        if self.artifact_store is not None:
            self.artifact_store.record(
                operation, "success", command=command, artifacts=artifacts
            )
        return result.stdout

    # -- plan staging --------------------------------------------------------
    def stage_plan(self, plan: Path) -> str:
        """Return the path the writer should read, copying it if remote.

        Local runs use the plan where it already is. Remote runs scp it to
        ``remote_plan_dir`` first; the generator host and the Bluetooth host
        share no filesystem.
        """
        if self.ssh_host is None:
            return str(plan)
        remote_path = f"{self.remote_plan_dir.rstrip('/')}/{plan.name}"
        command = ["scp", *self.ssh_options, str(plan), f"{self.ssh_host}:{remote_path}"]
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BenlinkError(f"could not copy plan to {self.ssh_host}: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenlinkError(
                f"could not copy plan to {self.ssh_host} ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        return remote_path

    def _transport_args(self, *, rfcomm: str | None, ble: str | None) -> list[str]:
        if rfcomm and ble:
            raise BenlinkError("specify either rfcomm or ble, not both")
        if rfcomm:
            return ["--rfcomm", rfcomm]
        if ble:
            return ["--ble", ble]
        raise BenlinkError("a transport is required: pass rfcomm=MAC or ble=ADDRESS")

    # -- operations ----------------------------------------------------------
    def dry_run(self, plan: Path) -> str:
        """Expand and print a plan without contacting a radio.

        Runs locally even when ``ssh_host`` is set: no radio is involved, so
        there is no reason to need the remote host to be up.
        """
        command = self._writer_command(["--plan", str(plan), "--dry-run"])
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BenlinkError(f"could not run benlink writer: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stdout or result.stderr).strip()
            raise BenlinkError(
                f"benlink dry-run failed ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        return result.stdout

    def verify(
        self,
        plan: Path,
        *,
        rfcomm: str | None = None,
        ble: str | None = None,
    ) -> str:
        """Read the radio back and diff it against ``plan``. Writes nothing.

        Useful on its own: it answers "is this radio still the codeplug we
        think it is" without touching memory.
        """
        remote_plan = self.stage_plan(plan)
        args = ["--plan", remote_plan, "--verify", *self._transport_args(rfcomm=rfcomm, ble=ble)]
        return self._run(args, artifacts=(plan,))

    def apply(
        self,
        plan: Path,
        *,
        rfcomm: str | None = None,
        ble: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Back up, write the plan, then read back and verify.

        Requires an explicit ``confirm=True`` safety token. A nonzero exit
        from the writer -- including a post-write verify mismatch -- becomes
        a ``BenlinkError`` carrying the diff, and the radio can be restored
        with :meth:`rollback`.
        """
        if not confirm:
            raise BenlinkError("refusing to write to a radio without confirm=True")
        remote_plan = self.stage_plan(plan)
        args = ["--plan", remote_plan, "--apply", *self._transport_args(rfcomm=rfcomm, ble=ble)]
        return self._run(args, artifacts=(plan,))

    def rollback(
        self,
        *,
        backup: str | None = None,
        rfcomm: str | None = None,
        ble: str | None = None,
    ) -> str:
        """Restore a full backup, newest one unless ``backup`` names another.

        The backup path is on the writer's host, which for a remote run is
        ``ssh_host`` rather than this machine.
        """
        args = ["--rollback", *self._transport_args(rfcomm=rfcomm, ble=ble)]
        if backup:
            args.extend(["--backup", backup])
        return self._run(args)
