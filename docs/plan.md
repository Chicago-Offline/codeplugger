# Codeplugger: intent, vision, and plan

## Intent

Programming a radio should be a deterministic build, not a manual CPS
session. Codeplugger compiles shared RF facts (SSRF) and user-owned profiles
into validated codeplugs and, where possible, programs radios directly —
so a fleet of radios can be kept consistent, reproducible, and auditable
from version-controlled inputs.

## Vision

Codeplugger is a **general-purpose codeplug compiler**. Vendor CPS tools and
their file formats are replaceable backends, not the workflow. The stable
core is:

```text
SSRF data + overlays + profile
  -> resolved, radio-neutral codeplug (validated against capabilities)
  -> exporter or programming backend for the target radio
```

Any radio should be supportable by adding a capabilities definition plus an
exporter or backend, without touching input resolution. Long term, the
preferred backend for each radio is headless programming: no interactive CPS
step between the resolved codeplug and the hardware.

## Guiding principles

- Generated codeplugs are disposable build artifacts, never a data source.
  Changes flow back into SSRF data or profiles and get regenerated.
- Profiles hold selection and ordering policy; RF facts live in SSRF.
- Radio writes are gated: explicit confirmation, known-firmware checks,
  read-back verification, and an operation log.
- Exporter defaults are named and reviewed, not copied wholesale from a CPS
  implementation.
- Fixtures in public repositories stay synthetic; no personal identities,
  unpublished frequencies, or real radio exports.

## Current state

Working today:

- Profile resolution and validation (`codeplugger-profile`): SSRF roots plus
  overlays, profile schema 0.1, radio capability limits including
  firmware-dependent limits, optional instance-registry join checks,
  severity-graded validation (hint/warning/critical) that reports every
  critical issue in one run.
- Profile policy beyond plain channel lists: RX group lists, scan lists
  (membership only), per-assignment TX contact, profile inheritance
  (`extends`, with `zones_only`/`omit_zones` variants and
  `--print-merged-profile` for review), and namespaced `extensions` at the
  profile and assignment level for exporter-native settings.
- Multi-identity DMR IDs: a registry instance may carry several `dmr_ids`
  (for example a licensed ID and an unregistered simplex ID) with a
  `default_dmr_id`; profiles bind one per zone or per assignment. Both the
  qdmr and P4 exporters emit one radio ID per identity actually used.
- Channel facts derived from SSRF: bandwidth from the ITU emission
  designator, AM mode for airband receive-only assignments.
- Registry-driven fleet dry run (`codeplugger-fleet`): resolves every
  registered radio, validates, and reports deterministic codeplug hashes.
- Retevis MateTalk P4: TOML export against a `p64tool decode` baseline,
  fleet-registry contacts, and a gated `p64tool` write backend with
  read-back verification.
- Baofeng DM-32: qdmr YAML export (`exporters/qdmr_yaml.py`, every default
  named and reviewed; DM-32UV structural minimums satisfied by explicit
  unused placeholders; profile `extensions.qdmr` for qdmr-native settings,
  contacts, and DMR APRS) and a gated `dmrconf` write backend
  (`backends/dmrconf.py`) mirroring the `p64tool` safety pattern:
  read-only `detect`/`read`/`verify`, explicit confirmation, pre-write
  verify gate, detected-radio identity gate, optional read-back archiving,
  operation-log auditing. `dmrconf verify --radio=dm32uv` passes on
  generated output and `encode`/`decode` round-trips it (skip-if-uninstalled
  tests keep this checked). **Hardware acceptance passed (2026-09):** a
  real DM-32UV write plus `dmrconf read` read-back matched the intended
  codeplug, so the path is considered safe and operational. Transport
  decision record: qdmr's `dmrconf` CLI (2026-09-08); the earlier NeonPlug
  import experiment
  ([neonplug-base-free-experiment.md](neonplug-base-free-experiment.md))
  is retained as evidence but that exporter path is dropped.
- DM-32UV button mappings are implemented in a local qdmr fork and
  hardware-validated on the green radio: SK1/SK2/P1/P2 short and long
  presses, long-press duration, and side-key lock survived encode/write/
  read-back, and the physical buttons behaved as configured. The qdmr
  fork's full 27-test suite passes; upstream PR preparation remains.
- DM-32 operational profiles: the shared community profile
  (`chioff_dm32_shared`, GMRS + MURS) and the `co_dm32_eric` registry entry
  live in `chioff-codeplugger-profiles-shared`; the profile resolves,
  validates, and passes `dmrconf verify --radio=dm32uv`. The operator's
  private repository carries the full personal DM-32 profile (97 channels /
  12 zones) and is fleet-ready. The fleet dry run is intentionally out of
  scope for the shared repository: community profiles are instance-agnostic
  (one profile, many radios), which the 1:1 profile-instance registry join
  does not model; shared profiles are validated per-profile with
  `codeplugger-profile` instead.
- Analog radios (UV-5R Mini, Ailunce HA2): CHIRP CSV export; CHIRP remains
  the upload interface (FM only). The HA2 has capabilities (1024 channels /
  16 zones, AM airband RX) but no HA2-specific exporter yet; it uses the
  generic CHIRP path.
- DM-32 analog channels/zones: NeonPlug `.neonplug` export
  (`exporters/neonplug/`, profile 0.1, FM only) as an interchange format for
  NeonPlug's own GUI, verified base-free against a pinned NeonPlug revision's
  real import path and DM-32UV byte codec. This is a manual-review interchange
  path, like CHIRP for the UV-5R Mini — separate from, and not a replacement
  for, the `dmrconf` headless write backend below.
- Artifacts: self-contained HTML and Markdown radio references (tones,
  color code, timeslot, bandwidth, power, scan lists, RX groups) plus a
  JSON Lines operation log for generation and device operations.
- Synthetic DM-32 profile coverage: the public
  [`chioff-codeplugger-profiles-test`](https://github.com/Chicago-Offline/chioff-codeplugger-profiles-test)
  repository owns the canonical synthetic DM-32 profile; its integration
  workflow resolves assignments across `ssrf-lite` and `chioff-ssrf-test`,
  validates against this repository's DM-32 capabilities, and generates
  qdmr YAML. Small self-contained fixtures remain here for fast unit tests.

## Now: operate the DM-32 path, deepen it upstream, then widen the qdmr backend

The DM-32 headless path is complete and hardware-verified. The current
focus is keeping it operational, closing the feature gap against the OEM
tooling by contributing to qdmr, and reusing the `dmrconf` investment:

1. **Run the DM-32 path in anger.** Program the real fleet from the private
   profile repository, keep the operation log and read-back archives as the
   audit trail, and feed any on-radio surprises back into SSRF data,
   profiles, or exporter defaults (never into the generated codeplug).
2. **Grow the DM-32 feature set by contributing to qdmr.** Where NeonPlug
   (and the OEM CPS) can set something the `dmrconf` path cannot, the fix
   belongs upstream, not in a codeplugger-side byte patcher. qdmr's
   `DM32UVCodeplug` already decodes and encodes the relevant bytes
   (`GeneralSettingsElement`: call/standby/channel-name/zone-name colors,
   side-key and P1/P2 short/long-press `KeyFunction`s, long-press duration,
   side-key lock, backlight, date format, boot display; `ChannelElement`:
   key index) but does not surface them in the configuration model, so
   they are neither readable nor writable from YAML. The work is a
   `DM32UVExtension` in the pattern of qdmr's existing AnyTone/Radioddity/
   TYT extensions, wired into `GeneralSettingsElement::decode/encode`, plus
   `dmrconf verify` limits and docs. Sequence, one PR each, smallest first:
   1. display colors (unblocks the long-standing screen-color gap),
   2. programmable key functions (SK1/SK2/P1/P2 short and long press,
      long-press duration, side-key lock),
   3. remaining general settings NeonPlug exposes (backlight, date
      format, roger tone, APO, and so on),
   4. per-channel extras (key index, any DM-32-only channel flags).
   Each PR is validated against a real radio via read/write round-trip.
   On the codeplugger side, expose each landed setting through the
   existing `extensions.qdmr` passthrough first; promote something to
   first-class profile schema only if it turns out to be radio-neutral
   policy (button mappings are the likely candidate). NeonPlug's byte
   codec and the
   [base-free experiment](neonplug-base-free-experiment.md) serve as the
   reference for field meanings, not as a delivery path. Until a PR is
   released, pin the exporter's `QDMR_CONFIG_VERSION` and tests to the
   qdmr revision that carries it and document the required build.
3. **More qdmr-supported radios via `capabilities.json` only.** The qdmr
   YAML exporter and `dmrconf` backend are already radio-neutral; adding a
   radio qdmr supports should mostly be a capabilities file plus a
   `dmrconf verify --radio=<key>` skip-if-uninstalled test. Candidates in
   rough order of community demand: AnyTone AT-D868UV/878UV/578UV, TYT
   MD-UV380/UV390 and Retevis RT3S, Radioddity GD-77, BTECH DMR-6X2,
   Baofeng DM-1701. Each needs a named, reviewed set of exporter defaults
   where qdmr's limits differ from the DM-32UV (placeholders, name lengths,
   list minimums).
4. **OpenGD77 via `dmrconf`.** `radios/opengd77/` exists and is empty.
   Capabilities plus the qdmr path first; an OpenGD77 CPS CSV exporter only
   if the `dmrconf` path leaves gaps.

Known gaps:

- DM-32 display colors, button functions, and most general settings are
  not reachable through `dmrconf` YAML today; tracked by item 2 above.
- Scan lists are membership-only (no priority channels) across exporters.

## Later

- AnyTone CPS CSV export-only path, if the qdmr route proves insufficient
  for AnyTone owners.
- `dmrconfig` / `editcp` backends are skipped unless qdmr coverage gaps
  appear.

## In flight

- Capturing Retevis P4 CPS v1.5 reference data under
  `radios/retevis_matetalk_p4/reference/` (user manual captured; CPS
  field-mapping notes still to come).

## Deferred (intentionally out of scope for now)

Until the explicit-ID workflow is proven end to end on more radios:

- Profile selectors (anything beyond explicit ordered assignment IDs)
- First-class button-mapping and display settings in the profile schema;
  they arrive first as `extensions.qdmr` passthrough once the upstream
  qdmr work in the Now section lands
- Scan-list priority channels
- Radios outside the qdmr / CHIRP / p64tool backends already in the tree

## Non-goals

- Becoming a CPS GUI or codeplug editor.
- Treating any generated file or radio read as authoritative data.
- Supporting codeplug round-trip editing; the flow is one-way from sources.
