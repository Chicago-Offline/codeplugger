#!/usr/bin/env python3
"""Build downloadable community codeplugs into ``site/codeplugs/``.

Reads ``site/codeplug-sources.json``, resolves every listed profile against the
SSRF roots it names, and writes the export files, an HTML reference, the
resolved radio-neutral codeplug, and a ``SHA256SUMS`` per profile.

This is deliberately also an integration test. A profile that no longer
resolves, an SSRF change that pushes a channel name past a radio's limit, or an
exporter regression fails the build instead of surfacing at a bench session.

Usage::

    python tools/build_site_artifacts.py
    python tools/build_site_artifacts.py --checkout-root /tmp/checkouts
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RADIOS_DIR = REPO_ROOT / "radios"
SITE_DIR = REPO_ROOT / "site"
SOURCES_JSON = SITE_DIR / "codeplug-sources.json"
RADIOS_JSON = SITE_DIR / "radios.json"
DEFAULT_OUT = SITE_DIR / "codeplugs"

# Export columns in site/radios.json that this builder can produce from source
# data alone, mapped to the extension the download gets.
SOURCE_FORMATS = {
    "chirp_csv": ("chirp-csv", ".csv"),
    "qdmr_yaml": ("qdmr-yaml", ".qdmr.yaml"),
}

# Export columns that exist but cannot be built from sources. The P4 exporter
# patches a TOML baseline produced by `p64tool decode`, which is a read off a
# specific radio -- not something CI can synthesize, and not something we would
# want to publish if it could.
UNBUILDABLE_FORMATS = {
    "p64_toml": "needs a p64tool decode baseline read from the radio",
    "oem_cps": "vendor CPS format, not a codeplugger exporter",
}


class BuildError(RuntimeError):
    """A data problem the author has to fix by hand."""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def load_sources() -> list[dict]:
    data = json.loads(SOURCES_JSON.read_text())
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise BuildError(f"{SOURCES_JSON} has no 'sources' list")
    return sources


def load_radio_rows() -> dict[str, dict]:
    data = json.loads(RADIOS_JSON.read_text())
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise BuildError(f"{RADIOS_JSON} has no 'rows' list")
    return {row["id"]: row for row in rows}


def resolve_checkout(checkout_root: Path, name: str, *, where: str) -> Path:
    path = checkout_root / name
    if not path.is_dir():
        raise BuildError(
            f"{where}: no checkout of {name!r} at {path}. Clone it there or "
            "pass --checkout-root."
        )
    return path


def git_revision(path: Path) -> str | None:
    """Record which revision of a source repository produced a codeplug."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------


def formats_for_radio(radio_id: str, row: dict, capabilities: dict) -> list[str]:
    """Decide the downloads from the radio's own support-table row.

    The support table is the promise; this is the delivery. Deriving one from
    the other is what stops the site advertising a CHIRP CSV column for a radio
    and then not offering the file.
    """
    formats = []
    for column, (fmt, _ext) in SOURCE_FORMATS.items():
        if row.get(column) and _column_supported(row[column]):
            formats.append(fmt)
    # benlink has no export column of its own: it is the headless backend for
    # radios with a `benlink` capabilities block, and the plan is its artifact.
    if "benlink" in capabilities:
        formats.append("benlink-plan")
    if not formats:
        unbuildable = [
            f"{column} ({reason})"
            for column, reason in UNBUILDABLE_FORMATS.items()
            if _column_supported(row.get(column))
        ]
        detail = f"; it supports only {', '.join(unbuildable)}" if unbuildable else ""
        raise BuildError(
            f"{radio_id}: no downloadable format{detail}. Remove its profiles "
            f"from {SOURCES_JSON.name} or add an exporter."
        )
    return formats


def _column_supported(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("supported"))
    return bool(value)


def render_exports(codeplug: Any, formats: list[str], capabilities: dict) -> dict[str, str]:
    from codeplugger.exporters.benlink_plan import benlink_plan_json_from_resolved
    from codeplugger.exporters.chirp_csv import chirp_csv_from_resolved
    from codeplugger.exporters.qdmr_yaml import qdmr_yaml_from_resolved

    out: dict[str, str] = {}
    for fmt in formats:
        if fmt == "chirp-csv":
            out[".csv"] = chirp_csv_from_resolved(codeplug)
        elif fmt == "qdmr-yaml":
            out[".qdmr.yaml"] = qdmr_yaml_from_resolved(codeplug)
        elif fmt == "benlink-plan":
            out[".benlink.json"] = benlink_plan_json_from_resolved(
                codeplug, capabilities=capabilities, generated_by="codeplugger"
            )
        else:  # pragma: no cover - formats_for_radio controls the set
            raise BuildError(f"unhandled format {fmt!r}")
    return out


def build_profile(
    profile_path: Path,
    ssrf_roots: list[Path],
    radio_rows: dict[str, dict],
    source_dir: Path,
) -> dict:
    from codeplugger.artifacts import html_reference_from_resolved
    from codeplugger.profile import _load_and_validate_profile, _load_capabilities
    from codeplugger.resolved import build_codeplug

    profile, documents, instance_metadata, report = _load_and_validate_profile(
        profile_path, ssrf_roots, radio_root=RADIOS_DIR
    )
    codeplug = build_codeplug(
        profile, documents, instance_metadata, radio_root=RADIOS_DIR, report=report
    )

    radio_id = codeplug.radio_id
    row = radio_rows.get(radio_id)
    if row is None:
        raise BuildError(f"{profile_path}: radio {radio_id!r} has no {RADIOS_JSON.name} row")
    capabilities = _load_capabilities(radio_id, RADIOS_DIR)

    profile_id = profile.get("id", profile_path.stem)
    files = dict(
        render_exports(codeplug, formats_for_radio(radio_id, row, capabilities), capabilities)
    )
    files[".html"] = html_reference_from_resolved(
        codeplug, radio_name=capabilities.get("name")
    )
    # The resolved model is what the published hash is taken over, so ship it:
    # the determinism claim is only checkable if the hashed thing is available.
    resolved_json = codeplug.to_json()
    files[".resolved.json"] = resolved_json

    out_dir = source_dir / radio_id / profile_id
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    for suffix, text in sorted(files.items()):
        name = f"{profile_id}{suffix}"
        (out_dir / name).write_text(text, encoding="utf-8")
        written.append(
            {
                "name": name,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "bytes": len(text.encode("utf-8")),
            }
        )
    (out_dir / "SHA256SUMS").write_text(
        "".join(f"{f['sha256']}  {f['name']}\n" for f in written), encoding="utf-8"
    )

    return {
        "profile_id": profile_id,
        "profile_name": profile.get("name", profile_id),
        "radio": radio_id,
        "radio_name": capabilities.get("name"),
        "dir": f"{radio_id}/{profile_id}",
        "zones": len(codeplug.zones),
        "channels": len(codeplug.channels),
        # Same digest codeplugger-fleet reports, over the same bytes.
        "resolved_sha256": hashlib.sha256(resolved_json.encode("utf-8")).hexdigest(),
        "warnings": [issue.format() for issue in report.non_critical()],
        "files": written,
    }


def build_source(source: dict, checkout_root: Path, radio_rows: dict, out_root: Path) -> dict:
    from codeplugger.profile import installed_ssrf_root

    source_id = source["id"]
    where = f"source {source_id!r}"
    checkout = source.get("checkout")
    if checkout is None:
        # Profiles that ship in this repository and resolve against the
        # ssrf-lite revision pyproject.toml pins, so CI publishes them with
        # nothing cloned and the quick start and the downloads cannot drift.
        profiles_dir = REPO_ROOT
        ssrf_roots = [installed_ssrf_root()]
        revisions = {"codeplugger": git_revision(REPO_ROOT)}
    else:
        profiles_dir = resolve_checkout(checkout_root, checkout, where=where)
        ssrf_roots = [
            resolve_checkout(checkout_root, root["checkout"], where=where) / root["path"]
            for root in source["ssrf_roots"]
        ]
        revisions = {
            checkout: git_revision(profiles_dir),
            **{
                root["checkout"]: git_revision(checkout_root / root["checkout"])
                for root in source["ssrf_roots"]
            },
        }
    for root in ssrf_roots:
        if not root.is_dir():
            raise BuildError(f"{where}: no SSRF root at {root}")

    built = []
    for rel in source["profiles"]:
        profile_path = profiles_dir / rel
        if not profile_path.is_file():
            raise BuildError(f"{where}: no profile at {profile_path}")
        entry = build_profile(
            profile_path,
            ssrf_roots,
            radio_rows,
            out_root / source_id,
        )
        entry["profile_path"] = rel
        entry["dir"] = f"{source_id}/{entry['dir']}"
        built.append(entry)

    return {
        "id": source_id,
        "name": source["name"],
        "description": source.get("description"),
        # Optional per-language overrides of name/description, keyed by lang.
        "i18n": source.get("i18n", {}),
        "repo": source.get("repo"),
        "revisions": revisions,
        "profiles": built,
    }


# --------------------------------------------------------------------------
# downloads page
# --------------------------------------------------------------------------

SITE_URL = "https://codeplugger.chicagooffline.com/"

# One downloads page per language. ``page`` is the path under the site root;
# the Spanish tree mirrors the English one under ``es/`` so relative links
# between the two index pages work the same way in both.
PAGES = {
    "en": "codeplugs/",
    "es": "es/codeplugs/",
}

STRINGS = {
    "en": {
        "switch": ("es", "Español"),
        "title": "Community codeplugs — codeplugger",
        "description": (
            "Prebuilt community codeplugs compiled by codeplugger. Download a "
            "file and import it -- no clone, no install."
        ),
        "generated": "Generated",
        "heading": "Community codeplugs",
        "lead": (
            "Prebuilt and validated by CI from public RF data and public profiles. "
            "Download the file for your radio and import it &mdash; no clone, no "
            "install. These are build artifacts: they are regenerated from source "
            "on every change, so program your radio from a fresh download rather "
            "than editing one."
        ),
        "profiles_link": "Profiles ↗",
        "built_from": "Built from",
        "file": "File",
        "purpose": "What it is for",
        "resolved_sha": "Resolved codeplug SHA-256",
        "zone": ("zone", "zones"),
        "channel": ("channel", "channels"),
        "formats": {
            ".csv": ("CHIRP CSV", "Import in CHIRP, then upload to the radio."),
            ".qdmr.yaml": ("qdmr YAML", "Open in qdmr, or write with dmrconf."),
            ".benlink.json": ("benlink plan", "Region plan for the benlink backend."),
            ".html": ("Printable reference", "Channel list to print or keep on a phone."),
            ".resolved.json": (
                "Resolved codeplug",
                "Radio-neutral model the hash is taken over.",
            ),
        },
    },
    "es": {
        "switch": ("en", "English"),
        "title": "Codeplugs comunitarios — codeplugger",
        "description": (
            "Codeplugs comunitarios precompilados por codeplugger. Descarga un "
            "archivo e impórtalo: sin clonar, sin instalar."
        ),
        "generated": "Generado",
        "heading": "Codeplugs comunitarios",
        "lead": (
            "Precompilados y validados por CI a partir de datos de RF públicos y "
            "perfiles públicos. Descarga el archivo para tu radio e impórtalo: sin "
            "clonar, sin instalar. Son artefactos de compilación: se regeneran "
            "desde la fuente con cada cambio, así que programa tu radio desde una "
            "descarga reciente en lugar de editar una."
        ),
        "profiles_link": "Perfiles ↗",
        "built_from": "Compilado desde",
        "file": "Archivo",
        "purpose": "Para qué sirve",
        "resolved_sha": "SHA-256 del codeplug resuelto",
        "zone": ("zona", "zonas"),
        "channel": ("canal", "canales"),
        "formats": {
            ".csv": ("CHIRP CSV", "Importar en CHIRP y luego cargar en la radio."),
            ".qdmr.yaml": ("qdmr YAML", "Abrir en qdmr o escribir con dmrconf."),
            ".benlink.json": ("plan benlink", "Plan de regiones para el backend benlink."),
            ".html": (
                "Referencia imprimible",
                "Lista de canales para imprimir o guardar en el teléfono.",
            ),
            ".resolved.json": (
                "Codeplug resuelto",
                "Modelo neutral de radio sobre el que se calcula el hash.",
            ),
        },
    },
}

def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def plural(count: int, noun: str | tuple[str, str]) -> str:
    if isinstance(noun, str):
        noun = (noun, noun + "s")
    return f"{count} {noun[0]}" if count == 1 else f"{count} {noun[1]}"


def rel_to_root(page: str) -> str:
    """Relative path from a page directory back to the site root."""
    return "../" * page.count("/")


def render_profile_card(entry: dict, lang: str = "en", files: str = "") -> str:
    s = STRINGS[lang]
    base = f"{files}{esc(entry['dir'])}"
    rows = []
    for f in entry["files"]:
        suffix = f["name"][len(entry["profile_id"]):]
        label, blurb = s["formats"].get(suffix, (suffix, ""))
        rows.append(
            "<tr>"
            f'<td><a href="{base}/{esc(f["name"])}">{esc(label)}</a></td>'
            f"<td>{esc(blurb)}</td>"
            f'<td><code>{esc(f["sha256"][:16])}…</code></td>'
            "</tr>"
        )
    return (
        f'<article id="{esc(entry["profile_id"])}">'
        f'<h3>{esc(entry["profile_name"])}</h3>'
        f'<p class="meta">{esc(entry["radio_name"] or entry["radio"])} · '
        f'{plural(entry["zones"], s["zone"])} · '
        f'{plural(entry["channels"], s["channel"])} · '
        f'<code>{esc(entry["profile_id"])}</code></p>'
        '<div class="matrix-scroll"><table class="matrix"><thead><tr>'
        f"<th>{s['file']}</th><th>{s['purpose']}</th><th>SHA-256</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        f'<p class="meta">{s["resolved_sha"]} '
        f'<code>{esc(entry["resolved_sha256"])}</code> · '
        f'<a href="{base}/SHA256SUMS">SHA256SUMS</a></p>'
        "</article>"
    )


def render_page(manifest: dict, lang: str = "en") -> str:
    s = STRINGS[lang]
    page = PAGES[lang]
    root = rel_to_root(page)
    # The download files live only under the English directory.
    files = "" if lang == "en" else f"{root}{PAGES['en']}"
    other_lang, other_label = s["switch"]

    sections = []
    for source in manifest["sources"]:
        text = {**source, **source.get("i18n", {}).get(lang, {})}
        revisions = " · ".join(
            f"<code>{esc(name)}@{esc((rev or 'unknown')[:12])}</code>"
            for name, rev in sorted(source["revisions"].items())
        )
        repo = source.get("repo")
        link = (
            f' <a href="{esc(repo)}" target="_blank" rel="noopener">{s["profiles_link"]}</a>'
            if repo
            else ""
        )
        sections.append(
            f'<section id="{esc(source["id"])}"><div class="wrap">'
            f'<h2>{esc(text["name"])}</h2>'
            f'<p class="sub">{esc(text.get("description"))}{link}</p>'
            '<div class="codeplugs">'
            + "".join(render_profile_card(p, lang, files) for p in source["profiles"])
            + "</div>"
            f'<p class="meta">{s["built_from"]} {revisions}</p>'
            "</div></section>"
        )

    alternates = "\n".join(
        f'  <link rel="alternate" hreflang="{code}" href="{SITE_URL}{path}" />'
        for code, path in PAGES.items()
    )
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="description" content="{s["description"]}" />
  <link rel="canonical" href="{SITE_URL}{page}" />
{alternates}
  <link rel="alternate" hreflang="x-default" href="{SITE_URL}{PAGES["en"]}" />
  <title>{s["title"]}</title>
  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg" />
  <link rel="stylesheet" href="{root}style.css" />
</head>
<body>
  <header>
    <div class="wrap">
      <a class="brand" href="{root}{"" if lang == "en" else lang + "/"}"><img src="{root}logo.svg" alt="" /> codeplugger</a>
      <a class="lang" href="{root}{PAGES[other_lang]}" lang="{other_lang}" hreflang="{other_lang}">{other_label}</a>
      <a class="repo-link" href="https://github.com/Chicago-Offline/codeplugger" target="_blank" rel="noopener">GitHub ↗</a>
    </div>
  </header>
  <main id="top">
    <div class="hero"><div class="wrap"><div>
      <p class="eyebrow">{s["generated"]} {esc(manifest["generated_at"])}</p>
      <h1>{s["heading"]}</h1>
      <p class="lead">
        {s["lead"]}
      </p>
    </div></div></div>
    {"".join(sections)}
  </main>
</body>
</html>
"""


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout-root",
        type=Path,
        default=REPO_ROOT.parent,
        help="directory holding the source repository checkouts "
        "(default: this repository's parent)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    try:
        radio_rows = load_radio_rows()
        manifest = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sources": [
                build_source(source, args.checkout_root, radio_rows, args.out)
                for source in load_sources()
            ],
        }
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # --out is the English codeplugs directory; the other languages' pages
    # sit beside it at <site>/<lang>/codeplugs/ and link back into it.
    site_root = args.out.parent
    for lang, page in PAGES.items():
        page_dir = args.out if lang == "en" else site_root / page
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_page(manifest, lang), encoding="utf-8")

    for source in manifest["sources"]:
        for entry in source["profiles"]:
            print(
                f"{source['id']}/{entry['profile_id']}: {entry['radio']}, "
                f"{entry['channels']} channels, {len(entry['files'])} files"
            )
            for warning in entry["warnings"]:
                print(f"  warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
