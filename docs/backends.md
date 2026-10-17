# Exporters and programming backends

A resolved codeplug leaves codeplugger one of two ways: as a file an external
CPS tool imports, or through a programming backend that drives the radio
itself. The GPL tools (`chirp-writer`, `dmrconf`, `p64tool`) are reached as
separate processes, never imports; `benlink` is Apache-2.0 and is imported.
Every write is gated: an explicit `confirm`, a backup taken before anything is
overwritten, and a read-back afterwards. `codeplugger program` wires these
gates for you; this page documents each path for when you need the pieces
directly.

| Radio family | Exporter | Backend | Write verified on hardware |
|---|---|---|---|
| Analog (CHIRP-supported) | CHIRP CSV | `chirp-writer` | FT-2800M, BF-1909 (driver round trip) |
| Baofeng DM-32 | qdmr YAML | `dmrconf` | yes |
| DM-1701, OpenGD77 | qdmr YAML | `dmrconf` | DM-1701 OEM, RT3S OpenGD77 |
| Retevis MateTalk P4 | P4 TOML | `p64tool` | yes |
| Benshi (VR-N76, UV-Pro) | benlink plan | `benlink` | no |

Per-radio status, with the evidence behind each badge, is on the
[project site](https://codeplugger.chicagooffline.com/) and in
[plan.md](plan.md).

## CHIRP CSV and `chirp-writer`

For analog radios codeplugger emits CHIRP-compatible CSV. That CSV can either
be imported in the CHIRP GUI and uploaded from there, or handed to the gated
`chirp-writer` backend, which programs the radio without a GUI step.

Generate CHIRP CSV from a validated profile:

```bash
uv run codeplugger-profile path/to/profile.yml \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../chioff-ssrf-test/ssrf \
  --output-format chirp-csv > output.csv
```

Then import `output.csv` in CHIRP and upload from CHIRP to the radio.

Notes:

- CHIRP CSV export currently supports FM channels only.
- Any non-FM channel selected by the profile fails export with an explicit
  error.
- Channel order in the CSV matches resolved profile order.
- Scan exclusion is set per assignment through `extensions.chirp`; see
  [profiles.md](profiles.md#excluding-channels-from-scan-chirp).

### Headless writes

CHIRP is GPL-3 and codeplugger is Apache-2.0, so CHIRP is reached the same way
`dmrconf` and `p64tool` are: as a separate process, never an import. The
[`chirp-writer`](https://github.com/Chicago-Offline/chirp-writer) helper does
the CHIRP-linked work and must be installed separately, in an environment where
`chirp` is importable. CHIRP's own `chirpc` cannot substitute for it: `chirpc`
never applies the driver's baud rate when opening the port, and it has no
CSV-import path at all.

Which driver addresses a radio lives in that radio's `capabilities.json`:

```json
"chirp": {
  "driver": "Yaesu_VX-177",
  "model": "Yaesu VX-177",
  "clone_mode": "manual"
}
```

`model` is the identity the connected radio must report, which is often not the
name on the case: an FT-277R reports as a Yaesu VX-177. `clone_mode` is
`manual` when an operator must put the radio into clone mode for every
transfer, and `on_demand` when the radio answers whenever the cable is
connected.

```python
from pathlib import Path
import json

from codeplugger.artifacts import ArtifactStore
from codeplugger.backends.chirp import ChirpTarget, ChirpWriter

capabilities = json.loads(Path("radios/retevis_c64/capabilities.json").read_text())
target = ChirpTarget.from_capabilities(capabilities)
store = ArtifactStore(Path("path/to/profiles"), "retevis_c64", "jhm_c64_01")

ChirpWriter(artifact_store=store).write(
    Path("output.csv"),
    "/dev/cu.usbserial-110",
    target=target,
    backup=Path("before.img"),
    read_back=Path("after.img"),
    confirm=True,
)
```

The gates, in order: `confirm=True`; a `read_back` path unless `verify=False`
is passed explicitly; and, inside the helper, a mandatory pre-write backup
whose reported model must match `target.model`. Because the backup download
happens before the upload, a wrong radio is rejected before anything is
written. Afterwards the helper re-reads the radio and diffs it per channel
against what was sent; any mismatch raises rather than returning quietly.
Unlisted memories are erased, since the generated codeplug is the whole truth
for that radio.

A radio whose capabilities carry no `chirp` block is refused rather than
addressed with a guessed driver.

## qdmr YAML and `dmrconf`

The qdmr YAML exporter produces a codeplug for qdmr's `dmrconf` command-line
tool. Radio-wide settings and DMR APRS go through `extensions.qdmr` in the
profile; see [profiles.md](profiles.md#qdmr-settings-and-dmr-aprs-dm-32).

The `dmrconf` backend wraps the external binary. Its write gates, in order:
`confirm=True`; a headless `dmrconf verify` against the radio key, because
dmrconf's own write-time check does not abort; `detect` must report the
expected device name; and a full `read` into the backup path before anything
is overwritten. Neither the backup nor the read-back path may already exist,
so one session cannot destroy the record of an earlier one.

The read-back is an archived post-write image, not a verification: dmrconf
takes YAML in and gives a device image back, so there is nothing to diff it
against the way the CHIRP backend can. It proves the radio still reads after
the write and gives a recovery and audit artifact; it does not prove the write
was correct.

## P4 TOML and `p64tool`

The P4 exporter requires a TOML file produced by `p64tool decode` as its
baseline. It updates resolved channel and zone records in place and preserves
radio-wide settings, existing contacts, RX groups, and fields codeplugger does
not yet model. It can also explicitly set analog bandwidth, clear unused
channel/zone slots, and add contacts from a fleet registry:

```python
from codeplugger.exporters.p64_toml import p64_toml_from_resolved

output = p64_toml_from_resolved(
  resolved,
  baseline_toml,
  fleet_instances=registry["instances"],
  clear_unused_channels=True,
  analog_bandwidth_khz=25.0,
  digital_contact_index=1,
  digital_rx_group_index=1,
)
```

When `fleet_instances` is supplied, the target radio's `dmr_id` becomes
`general.radio_dmr_id` and each registry radio is appended as a private
contact. Contact and RX-group indices must refer to records already present in
the baseline; the exporter does not invent talkgroup semantics.

The external `p64tool` backend wrapper provides read-only `info`, `read`, and
`roundtrip` operations plus an explicit `confirm=True` write operation. The
write takes a mandatory raw backup first, refusing a path that already exists,
and always uses p64tool's known-firmware gate and read-back verification by
default.

## benlink plan and the `benlink` backend

Benshi radios (BTECH UV-Pro, Vero VR-N76) are programmed over Bluetooth
through [benlink](https://github.com/khusmann/benlink), installed with the
`benlink` extra. The exporter produces a region plan: these radios scope
channels to regions rather than a global pool, so an assignment repeated
across zones gets a slot in each region.

The backend's write gates, in order: `confirm=True`; a radio whose
capabilities are not marked `hardware_verified` additionally requires
`allow_unverified=True`; and the connected radio's self-reported region and
channel counts are checked against the plan before anything is written. No
codeplugger-generated plan has yet been written to a physical Benshi radio,
which is why no entry in that family carries `hardware_verified` and the site
flags their downloads.
