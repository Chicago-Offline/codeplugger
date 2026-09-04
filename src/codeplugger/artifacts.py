"""Audit records and generated artifacts for radio operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class OperationRecord:
    """One attempted codeplug generation or device operation."""

    operation: str
    status: str
    radio_id: str
    radio_instance_id: str
    command: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    detail: str | None = None
    timestamp: str = ""


class ArtifactStore:
    """Persist per-radio artifacts below a profiles repository."""

    def __init__(self, root: Path, radio_id: str, radio_instance_id: str) -> None:
        self.directory = root / ".artifacts" / radio_id / radio_instance_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / "operations.jsonl"

    def record(
        self,
        operation: str,
        status: str,
        *,
        command: Sequence[str] = (),
        artifacts: Sequence[Path] = (),
        detail: str | None = None,
    ) -> Path:
        """Append one timestamped operation record."""

        record = OperationRecord(
            operation=operation,
            status=status,
            radio_id=self.directory.parent.name,
            radio_instance_id=self.directory.name,
            command=tuple(command),
            artifacts=tuple(str(path) for path in artifacts),
            detail=detail,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record), sort_keys=True) + "\n")
        return self.log_path


def html_reference_from_resolved(codeplug: Any) -> str:
    """Render a self-contained printable reference for any resolved radio."""

    channels = {channel.reference: channel for channel in codeplug.channels}
    sections: list[str] = []
    for zone in codeplug.zones:
        rows = []
        for index, reference in enumerate(zone.channel_references, 1):
            channel = channels[reference]
            tx = (
                f"{channel.tx_frequency_mhz:.6f}"
                if channel.tx_frequency_mhz is not None
                else "RX only"
            )
            rows.append(
                "<tr>"
                f"<td>{index}</td><td>{html.escape(channel.display_name)}</td>"
                f"<td>{channel.rx_frequency_mhz:.6f}</td><td>{html.escape(tx)}</td>"
                f"<td>{html.escape(channel.mode or '')}</td>"
                f"<td>{html.escape(channel.service or '')}</td>"
                f"<td>{'Yes' if channel.tx_permitted else 'No'}</td>"
                f"<td>{html.escape(channel.notes or '')}</td>"
                "</tr>"
            )
        sections.append(
            f"<h2>{html.escape(zone.name)}</h2>"
            "<table><thead><tr><th>#</th><th>Name</th><th>RX</th><th>TX</th>"
            "<th>Mode</th><th>Service</th><th>TX permitted</th><th>Notes</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(codeplug.radio_instance_id)} codeplug</title>"
        "<style>body{font:14px -apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;"
        "color:#17202a;margin:2rem}h1{margin-bottom:.2rem}.meta{color:#59636e;"
        "margin-bottom:2rem}table{border-collapse:collapse;width:100%;margin-bottom:2rem}"
        "th,td{border:1px solid #c8ced4;padding:.45rem .55rem;text-align:left}"
        "th{background:#e9eef2}tr:nth-child(even){background:#f7f9fa}"
        "@media print{body{margin:0}h2{break-after:avoid}table{font-size:10pt}}"
        "</style></head><body>"
        f"<h1>{html.escape(codeplug.radio_id)}</h1>"
        f"<div class=\"meta\">Radio instance: {html.escape(codeplug.radio_instance_id)}"
        f"<br>Channels: {len(codeplug.channels)} | Zones: {len(codeplug.zones)}</div>"
        + "".join(sections)
        + "</body></html>\n"
    )


def write_html_reference(path: Path, codeplug: Any) -> None:
    """Write a resolved codeplug's printable reference."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_reference_from_resolved(codeplug), encoding="utf-8")


def write_profile_artifacts(root: Path, codeplug: Any) -> tuple[Path, Path]:
    """Write the HTML reference and generation log for a resolved radio."""

    store = ArtifactStore(root, codeplug.radio_id, codeplug.radio_instance_id)
    reference_path = store.directory / "reference.html"
    write_html_reference(reference_path, codeplug)
    store.record("generate", "success", artifacts=(reference_path,))
    return reference_path, store.log_path