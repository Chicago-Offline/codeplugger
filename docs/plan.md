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
- Artifacts: self-contained HTML radio reference plus a JSON Lines
  operation log for generation and device operations.

## Now: DM-32 headless programming

The near-term priority is programming the Baofeng DM-32 without an
interactive NeonPlug/CPS session, following the safety pattern established
by the P4 `p64tool` backend.

Sequenced work:

1. **Base-free codeplug generation.** The NeonPlug import experiment
   ([neonplug-base-free-experiment.md](neonplug-base-free-experiment.md))
   showed a device-derived base codeplug is not required at the import and
   channel/zone encoding boundary, but each exporter default (power,
   bandwidth, squelch, step frequency, unknown fields) must be named and
   reviewed rather than copied from `createDefaultChannel()`.
2. **Synthetic `.neonplug` validation.** Generate a synthetic codeplug,
   inspect imported values in the pinned NeonPlug revision, and confirm
   `validateChannelForEncoding()` passes for every emitted channel.
3. **Transport decision.** Choose between driving NeonPlug's write path
   headlessly and a direct serial protocol backend informed by `dm32-info`
   research. Stable findings become radio definitions or validation rules
   here; generation must not depend on research notes directly.
4. **Gated write backend.** Whatever the transport, the backend mirrors the
   `p64tool` wrapper: read-only info/read/roundtrip operations, explicit
   `confirm=True` writes, firmware gate, read-back verification, and
   operation-log auditing.
5. **Hardware smoke test.** A DM-32UV write and verification on a real
   radio is the acceptance gate before the path is considered safe.

## In flight

- Capturing Retevis P4 CPS v1.5 reference data under
  `radios/retevis_matetalk_p4/reference/`.

## Deferred (intentionally out of scope for now)

Until the explicit-ID workflow is proven end to end on more radios:

- Profile selectors (anything beyond explicit ordered assignment IDs)
- Scan lists, button mappings, display and exporter settings in profiles
- Contact policy beyond fleet-registry private contacts
- Radios beyond the current three (DM-32, MateTalk P4, UV-5R Mini)

## Non-goals

- Becoming a CPS GUI or codeplug editor.
- Treating any generated file or radio read as authoritative data.
- Supporting codeplug round-trip editing; the flow is one-way from sources.
