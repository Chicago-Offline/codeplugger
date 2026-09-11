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
  firmware-dependent limits, optional instance-registry join checks.
- Registry-driven fleet dry run (`codeplugger-fleet`): resolves every
  registered radio, validates, and reports deterministic codeplug hashes.
- Retevis MateTalk P4: TOML export against a `p64tool decode` baseline,
  fleet-registry contacts, and a gated `p64tool` write backend with
  read-back verification.
- Analog radios (UV-5R Mini): CHIRP CSV export; CHIRP remains the upload
  interface (FM only).
- DM-32 analog channels/zones: NeonPlug `.neonplug` export
  (`exporters/neonplug/`, profile 0.1, FM only) as an interchange format for
  NeonPlug's own GUI, verified base-free against a pinned NeonPlug revision's
  real import path and DM-32UV byte codec. This is a manual-review interchange
  path, like CHIRP for the UV-5R Mini — separate from, and not a replacement
  for, the `dmrconf` headless write backend below.
- Artifacts: self-contained HTML radio reference plus a JSON Lines
  operation log for generation and device operations.

## Now: DM-32 headless programming

The near-term priority is programming the Baofeng DM-32 without an
interactive CPS session, following the safety pattern established by the
P4 `p64tool` backend.

**Transport decision (resolved 2026-09-08): qdmr's `dmrconf` CLI.**
Upstream qdmr supports the DM-32UV (`--radio=dm32uv`, still flagged as
under development), and its extensible YAML codeplug format gives a
documented, radio-neutral interchange file plus headless `verify`,
`encode`, `read`, and `write` commands. The earlier NeonPlug import
experiment ([neonplug-base-free-experiment.md](neonplug-base-free-experiment.md))
is retained as evidence but the NeonPlug exporter path is dropped.

Sequenced work:

1. **qdmr YAML exporter.** *(done)* `exporters/qdmr_yaml.py` emits the
   extensible codeplug format from a resolved codeplug, with every default
   named and reviewed (verified against qdmr `e84d4b3a`). Structural
   minimums the DM-32UV encoder imposes (≥1 group-call contact, ≥1 RX
   group list) are satisfied by an explicit unused placeholder, not by
   inventing talkgroup policy; fleet-registry members become private
  contacts as on the P4. Profile `extensions.qdmr` can add qdmr-native
  settings, supporting contacts, DMR APRS systems, and non-generated channel
  properties. Screen colors remain blocked on upstream qdmr configuration
  model support.
2. **Headless validation.** *(done)* `dmrconf verify --radio=dm32uv`
   passes on generated output and `encode`/`decode` round-trips it; a
   skip-if-uninstalled test keeps this checked in CI-like runs.
3. **Gated write backend.** *(done)* `backends/dmrconf.py` mirrors the
   `p64tool` wrapper: read-only `detect`/`read`/`verify`, explicit
   `confirm=True` writes, a pre-write `verify` gate (dmrconf's own
   write-time check logs but does not abort), a detected-radio identity
   gate, optional post-write read-back archiving, and operation-log
   auditing.
4. **Hardware smoke test.** A DM-32UV write and verification on a real
   radio is the acceptance gate before the path is considered safe.
   Compare a post-write `dmrconf read` against the intended codeplug and
   spot-check on-radio behavior (zones, tones, color code, timeslot).
5. **Synthetic profile coverage.** *(done)* The public
  [`chioff-codeplugger-profiles-test`](https://github.com/Chicago-Offline/chioff-codeplugger-profiles-test)
  repository owns the canonical synthetic DM-32 profile. Its integration
  workflow resolves assignments across `ssrf-lite` and `chioff-ssrf-test`,
  validates against this repository's DM-32 capabilities, and generates
  qdmr YAML. Small self-contained fixtures remain here for fast unit tests.
6. **Operational profile and fleet coverage.** Build the intended DM-32
  profile and instance-registry entry in
  `chioff-codeplugger-profiles-shared` or an operator's private profile
  repository. Synthetic integration coverage does not replace a real fleet
  configuration, and physical identifiers do not belong in public test
  fixtures.

## In flight

- Capturing Retevis P4 CPS v1.5 reference data under
  `radios/retevis_matetalk_p4/reference/`.

## Deferred (intentionally out of scope for now)

Until the explicit-ID workflow is proven end to end on more radios:

- Profile selectors (anything beyond explicit ordered assignment IDs)
- Button mappings, display and exporter settings in profiles
- Radios beyond the current three (DM-32, MateTalk P4, UV-5R Mini)

## Non-goals

- Becoming a CPS GUI or codeplug editor.
- Treating any generated file or radio read as authoritative data.
- Supporting codeplug round-trip editing; the flow is one-way from sources.
