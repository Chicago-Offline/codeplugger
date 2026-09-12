"""Audit records and generated artifacts for radio operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


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


def _reference_header(
    codeplug: Any,
    radio_name: str | None,
    programmed_at: datetime | None,
) -> tuple[list[str], str]:
    """Build the shared heading parts and programmed timestamp for a reference."""

    metadata = codeplug.radio_instance or {}
    programmed_at = programmed_at or datetime.now(timezone.utc)
    if programmed_at.tzinfo is None:
        programmed_at = programmed_at.replace(tzinfo=timezone.utc)
    programmed = programmed_at.astimezone(timezone.utc).strftime("%Y.%m.%d %H:%M UTC")
    organization = metadata.get("organization", "Chicago Offline")
    model = radio_name or codeplug.radio_id.replace("_", " ").title()
    if " " in model:
        model = model.split(" ", 1)[1]
    identity = metadata.get("dmr_id", codeplug.radio_instance_id)
    color = metadata.get("tape_color") or metadata.get("case_color")
    heading_parts = [organization, model, str(identity)]
    if color:
        heading_parts.append(str(color).title())
    return heading_parts, programmed


def _contact_instances(
    codeplug: Any,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    """Return the contact-bearing instances for a reference, in registry order."""

    metadata = codeplug.radio_instance or {}
    instances = fleet_instances
    if instances is None and metadata.get("dmr_id") is not None:
        instances = {codeplug.radio_instance_id: metadata}
    return [
        instance
        for instance in (instances or {}).values()
        if instance.get("dmr_id") is not None
    ]


def _tone_text(ctcss_hz: Any, dcs_code: Any) -> str:
    """Describe one direction's analog squelch tone, or blank when carrier-only.

    CTCSS renders in Hz and DCS as a ``D``-prefixed code so the two encodings
    stay distinguishable at a glance on a printed card.
    """

    if ctcss_hz is not None:
        return f"{float(ctcss_hz):.1f}"
    if dcs_code is not None:
        code = str(dcs_code)
        return code if code.upper().startswith("D") else f"D{code}"
    return ""


def _timeslot_text(channel: Any) -> str:
    """Render the DMR timeslot, preferring the resolved single-slot choice."""

    if getattr(channel, "timeslot", None) is not None:
        return str(channel.timeslot)
    slots = getattr(channel, "timeslots", ()) or ()
    return "/".join(str(slot) for slot in slots)


def _channel_row_values(
    channel: Any,
    index: int,
    scan_list_names: Mapping[str, str] | None = None,
) -> list[str]:
    """Return one channel's reference cells in table-column order.

    ``scan_list_names`` maps scan-list id to display name so the channel table
    can show which list a channel scans under; ids fall through unmapped.
    """

    tx = (
        f"{channel.tx_frequency_mhz:.6f}"
        if channel.tx_frequency_mhz is not None
        else "RX only"
    )
    tones = getattr(channel, "tones", None)
    rx_tone = tx_tone = ""
    if tones is not None:
        rx_tone = _tone_text(tones.ctcss_rx_hz, tones.dcs_rx_code)
        tx_tone = _tone_text(tones.ctcss_tx_hz, tones.dcs_tx_code)
    color_code = getattr(channel, "color_code", None)
    bandwidth = getattr(channel, "bandwidth_khz", None)
    power = getattr(channel, "power_w", None)
    scan_list_id = getattr(channel, "scan_list_id", None) or ""
    scan_list = (scan_list_names or {}).get(scan_list_id, scan_list_id)
    return [
        str(index),
        channel.display_name,
        f"{channel.rx_frequency_mhz:.6f}",
        tx,
        channel.mode or "",
        f"{float(bandwidth):g}" if bandwidth is not None else "",
        f"{float(power):g}" if power is not None else "",
        rx_tone,
        tx_tone,
        "" if color_code is None else str(color_code),
        _timeslot_text(channel),
        scan_list,
        channel.service or "",
        "Yes" if channel.tx_permitted else "No",
        channel.notes or "",
    ]


CHANNEL_COLUMNS = (
    "#",
    "Name",
    "RX",
    "TX",
    "Mode",
    "BW kHz",
    "Power W",
    "Tone RX",
    "Tone TX",
    "CC",
    "TS",
    "Scan",
    "Service",
    "TX permitted",
    "Notes",
)
CONTACT_COLUMNS = ("#", "Name", "DMR ID", "Type")
SCAN_LIST_COLUMNS = ("#", "Scan list", "Channels", "Members")
RX_GROUP_COLUMNS = ("#", "RX group", "Talkgroups", "Members")


def _optional_counts(scan_lists: Sequence[Any], rx_groups: Sequence[Any]) -> str:
    """Render scan-list and RX-group counts only when the radio defines any.

    Radios with neither keep the original three-field summary line rather than
    carrying ``Scan lists: 0 | RX groups: 0`` noise.
    """

    parts = ""
    if scan_lists:
        parts += f" | Scan lists: {len(scan_lists)}"
    if rx_groups:
        parts += f" | RX groups: {len(rx_groups)}"
    return parts


def _scan_list_names(codeplug: Any) -> dict[str, str]:
    """Map scan-list id to display name for the channel table's Scan column."""

    return {
        scan_list.id: scan_list.name
        for scan_list in getattr(codeplug, "scan_lists", ()) or ()
    }


def _scan_list_row_values(scan_list: Any, index: int, channels: Mapping[str, Any]) -> list[str]:
    """Return one scan list's reference cells: name, size, and member names."""

    references = tuple(scan_list.channel_references)
    members = ", ".join(
        channels[reference].display_name
        for reference in references
        if reference in channels
    )
    return [str(index), scan_list.name, str(len(references)), members]


def _rx_group_row_values(group: Any, index: int, contact_names: Mapping[str, str]) -> list[str]:
    """Return one RX group's reference cells: name, size, and talkgroup names."""

    contact_ids = tuple(group.contact_ids)
    members = ", ".join(
        contact_names.get(contact_id, contact_id) for contact_id in contact_ids
    )
    return [str(index), group.name, str(len(contact_ids)), members]


def _ssrf_contact_names(codeplug: Any) -> dict[str, str]:
    """Map SSRF contact id to display name for RX group membership rendering."""

    names: dict[str, str] = {}
    for contact in getattr(codeplug, "contacts", ()) or ():
        contact_id = getattr(contact, "id", None)
        if contact_id is None:
            continue
        names[contact_id] = getattr(contact, "name", None) or contact_id
    return names


def _markdown_cell(value: Any) -> str:
    """Flatten a cell to one line: Markdown tables cannot span newlines."""

    return " ".join(str(value).split()).replace("|", "\\|")


def _markdown_row(values: Sequence[str]) -> str:
    """Render one Markdown table row, escaping cell-breaking pipes."""

    return "| " + " | ".join(_markdown_cell(v) for v in values) + " |"


def markdown_reference_from_resolved(
    codeplug: Any,
    *,
    radio_name: str | None = None,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
    programmed_at: datetime | None = None,
) -> str:
    """Render a resolved radio's reference as Markdown for in-repo review.

    Mirrors the HTML reference's structure so the two stay comparable, but
    renders as GitHub-viewable Markdown tables instead of a styled document.
    """

    heading_parts, programmed = _reference_header(
        codeplug, radio_name, programmed_at
    )
    contacts = _contact_instances(codeplug, fleet_instances)
    channels = {channel.reference: channel for channel in codeplug.channels}
    scan_lists = tuple(getattr(codeplug, "scan_lists", ()) or ())
    rx_groups = tuple(getattr(codeplug, "rx_groups", ()) or ())
    scan_names = _scan_list_names(codeplug)
    contact_names = _ssrf_contact_names(codeplug)

    lines = [
        f"# {' - '.join(heading_parts)}",
        "",
        f"Programmed: {programmed}",
        "",
        "Channels: "
        f"{len(codeplug.channels)} | Zones: {len(codeplug.zones)}"
        + _optional_counts(scan_lists, rx_groups)
        + f" | Contacts: {len(contacts)}",
        "",
        "## Zones / Channels",
    ]
    for zone_index, zone in enumerate(codeplug.zones, 1):
        lines += [
            "",
            f"### Zone {zone_index} - {zone.name}",
            "",
            _markdown_row(CHANNEL_COLUMNS),
            _markdown_row(["---"] * len(CHANNEL_COLUMNS)),
        ]
        for index, reference in enumerate(zone.channel_references, 1):
            lines.append(
                _markdown_row(
                    _channel_row_values(channels[reference], index, scan_names)
                )
            )

    if scan_lists:
        lines += [
            "",
            "## Scan Lists",
            "",
            _markdown_row(SCAN_LIST_COLUMNS),
            _markdown_row(["---"] * len(SCAN_LIST_COLUMNS)),
        ]
        for index, scan_list in enumerate(scan_lists, 1):
            lines.append(
                _markdown_row(_scan_list_row_values(scan_list, index, channels))
            )

    if rx_groups:
        lines += [
            "",
            "## RX Groups",
            "",
            _markdown_row(RX_GROUP_COLUMNS),
            _markdown_row(["---"] * len(RX_GROUP_COLUMNS)),
        ]
        for index, group in enumerate(rx_groups, 1):
            lines.append(
                _markdown_row(_rx_group_row_values(group, index, contact_names))
            )

    lines += [
        "",
        "## Contacts",
        "",
        _markdown_row(CONTACT_COLUMNS),
        _markdown_row(["---"] * len(CONTACT_COLUMNS)),
    ]
    for index, instance in enumerate(contacts, 1):
        dmr_id = instance["dmr_id"]
        lines.append(
            _markdown_row(
                [
                    str(index),
                    str(instance.get("dmr_contact_name", dmr_id)),
                    str(dmr_id),
                    "Private",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def html_reference_from_resolved(
    codeplug: Any,
    *,
    radio_name: str | None = None,
    fleet_instances: Mapping[str, Mapping[str, Any]] | None = None,
    programmed_at: datetime | None = None,
) -> str:
    """Render a self-contained printable reference for any resolved radio."""

    heading_parts, programmed = _reference_header(
        codeplug, radio_name, programmed_at
    )
    contact_rows = []
    for instance in _contact_instances(codeplug, fleet_instances):
        dmr_id = instance["dmr_id"]
        contact_rows.append(
            "<tr>"
            f"<td>{len(contact_rows) + 1}</td>"
            f"<td>{html.escape(str(instance.get('dmr_contact_name', dmr_id)))}</td>"
            f"<td>{dmr_id}</td><td>Private</td>"
            "</tr>"
        )

    channels = {channel.reference: channel for channel in codeplug.channels}
    scan_lists = tuple(getattr(codeplug, "scan_lists", ()) or ())
    rx_groups = tuple(getattr(codeplug, "rx_groups", ()) or ())
    scan_names = _scan_list_names(codeplug)
    contact_names = _ssrf_contact_names(codeplug)
    sections: list[str] = []
    for zone_index, zone in enumerate(codeplug.zones, 1):
        rows = []
        for index, reference in enumerate(zone.channel_references, 1):
            cells = "".join(
                f"<td>{html.escape(value)}</td>"
                for value in _channel_row_values(
                    channels[reference], index, scan_names
                )
            )
            rows.append(f"<tr>{cells}</tr>")
        header = "".join(f"<th>{html.escape(name)}</th>" for name in CHANNEL_COLUMNS)
        sections.append(
            f"<h3>Zone {zone_index} - {html.escape(zone.name)}</h3>"
            f"<table><thead><tr>{header}</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    def _table(columns: tuple[str, ...], rows: list[list[str]]) -> str:
        head = "".join(f"<th>{html.escape(name)}</th>" for name in columns)
        body = "".join(
            "<tr>"
            + "".join(f"<td>{html.escape(value)}</td>" for value in row)
            + "</tr>"
            for row in rows
        )
        return (
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        )

    list_sections = ""
    if scan_lists:
        list_sections += "<h2>Scan Lists</h2>" + _table(
            SCAN_LIST_COLUMNS,
            [
                _scan_list_row_values(scan_list, index, channels)
                for index, scan_list in enumerate(scan_lists, 1)
            ],
        )
    if rx_groups:
        list_sections += "<h2>RX Groups</h2>" + _table(
            RX_GROUP_COLUMNS,
            [
                _rx_group_row_values(group, index, contact_names)
                for index, group in enumerate(rx_groups, 1)
            ],
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(codeplug.radio_instance_id)} codeplug</title>"
        "<style>body{font:14px -apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;"
        "color:#17202a;margin:2rem}h1{margin-bottom:.4rem}h1+h3{margin-top:0}"
        "h3{color:#59636e}table{border-collapse:collapse;width:100%;margin-bottom:2rem}"
        "th,td{border:1px solid #c8ced4;padding:.35rem .4rem;text-align:left;"
        "white-space:nowrap}td:last-child,th:last-child{white-space:normal}"
        "th{background:#e9eef2}tr:nth-child(even){background:#f7f9fa}"
        "@media print{body{margin:0}h2,h3{break-after:avoid}table{font-size:10pt}}"
        "</style></head><body>"
        f"<h1>{html.escape(' - '.join(heading_parts))}</h1>"
        f"<h3>Programmed: {programmed}</h3>"
        f"<h3>Channels: {len(codeplug.channels)} | Zones: {len(codeplug.zones)}"
        + _optional_counts(scan_lists, rx_groups)
        + f" | Contacts: {len(contact_rows)}</h3>"
        "<h2>Zones / Channels</h2>"
        + "".join(sections)
        + list_sections
        + "<h2>Contacts</h2>"
        + "<table><thead><tr><th>#</th><th>Name</th><th>DMR ID</th>"
        "<th>Type</th></tr></thead><tbody>"
        + "".join(contact_rows)
        + "</tbody></table>"
        + "</body></html>\n"
    )


def write_html_reference(
    path: Path,
    codeplug: Any,
    **render_options: Any,
) -> None:
    """Write a resolved codeplug's printable reference."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        html_reference_from_resolved(codeplug, **render_options), encoding="utf-8"
    )


def write_markdown_reference(
    path: Path,
    codeplug: Any,
    **render_options: Any,
) -> None:
    """Write a resolved codeplug's Markdown reference."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        markdown_reference_from_resolved(codeplug, **render_options), encoding="utf-8"
    )


def write_profile_artifacts(
    root: Path,
    codeplug: Any,
    **render_options: Any,
) -> tuple[Path, Path]:
    """Write the HTML and Markdown references plus the log for a resolved radio."""

    store = ArtifactStore(root, codeplug.radio_id, codeplug.radio_instance_id)
    reference_path = store.directory / "reference.html"
    write_html_reference(reference_path, codeplug, **render_options)
    markdown_path = store.directory / "reference.md"
    write_markdown_reference(markdown_path, codeplug, **render_options)
    store.record("generate", "success", artifacts=(reference_path, markdown_path))
    return reference_path, store.log_path