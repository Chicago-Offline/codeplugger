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
- Analog radios (UV-5R Mini, Ailunce HA2, Retevis C64, Baofeng BF-888S, Baofeng
  W31E, Yaesu FT-270R / FT-277R / FT-2800M): CHIRP CSV export (FM only), either imported in
  the CHIRP GUI or written headlessly by the `chirp-writer` backend described
  below.
  The HA2 has capabilities
  (1024 channels / 16 zones, AM airband RX) but no HA2-specific exporter yet;
  it uses the generic CHIRP path. The C64 (64 channels, 136-174 / 400-480 MHz,
  no zone concept) likewise uses the generic CHIRP path; its capabilities are
  measured from a physical radio in `retevis-c64-info` and match the CHIRP C64
  driver, which is a Retevis C2 protocol variant. The FT-270R / FT-277R are
  the same submersible chassis in 2 m and 70 cm; their capabilities (200
  channels, 6-character names, ham-band TX inside a wider receive span, no
  reachable banks) come from CHIRP's VX-170 / VX-177 drivers and are
  bench-verified against both physical radios in `ft270-277-info`. The radios
  report the stock model IDs `AH022$` / `AH022U`, so stock CHIRP programs
  them with no patch, and a CHIRP upload to a physical FT-277R is confirmed.
  A MARS/CAP-modded radio gets its own radio id (`yaesu_ft277_mars`) rather
  than a flag or an override: transmit permission is the one capability that
  must never be inferred, and the instance registry already binds one
  physical radio to one radio id, so the mod is recorded where the radio is.
  The FT-2800M is a 2 m mobile on the same generic CHIRP path, with the same
  200 channels and 6-character names but its own driver
  (`chirp/drivers/ft2800.py`), which reports the name on the case instead of a
  VX alias. It ships as a stock/modded pair too, `yaesu_ft2800m` and
  `yaesu_ft2800m_mars`, and here the distinction is machine-readable: the
  clone ID block ends `02 00 b8` on a stock US radio and `03 00 b9` on one
  with the extended-TX mod, so the right radio id can be confirmed from a
  read rather than taken on trust. A bench radio read on 2026-09-14 sent the
  stock block and a 7680-byte codeplug, which is what `yaesu_ft2800m`
  records; the modded id has no physical instance yet. That read also made
  the point the `rx_only` span exists for: 47 of its 177 memories were public
  safety and marine VHF up to 161 MHz, which a stock radio stores and
  receives perfectly well and still cannot transmit on.
- Baofeng BF-C50 (`baofeng_bf_c50`): validation and CSV / markdown reference
  output only, with no programming path. CHIRP has no BF-C50 driver
  (chirpmyradio.com issue #10176 is still open, and the fork opened to build
  one, `emuehlstein/chirpnewfangs`, never got a driver committed), and the
  Retevis RB618 workaround circulating there transfers frequencies but not
  CTCSS/DCS, so its `capabilities.json` deliberately omits the `chirp` block
  and the backend refuses the radio rather than swapping in a model id that
  would silently write wrong tones. The entry gets the FT-270R band
  treatment: the advertised 400-470 MHz span is receive-only either side of a
  430-440 MHz transmit block, which is what the unit's CMIIT type approval
  (2020FP6711, FM handheld, amateur service) actually covers. That keeps the
  uncertified GMRS/FRS 462-467 MHz block rejected. 16 channels, operator-
  confirmed; with no screen a channel is only ever identified by its knob
  position, so channel names are operator documentation rather than data the
  radio stores. Other BF-C50 builds ship with different transmit permissions
  — the EU PMR446 one is programmed as an RB618 — and each would be a
  separate radio id.
- Baofeng BF-888S (`baofeng_bf888s`): 16 channels, no display and no
  channel-name field, generic CHIRP path plus headless `chirp-writer`
  programming. The case and CHIRP disagree on the name, and the write gate has
  to use CHIRP's: the driver is `H777Radio` in `chirp/drivers/h777.py`,
  nominally the Heng Shun Tong H-777 but registered as `Baofeng BF-888` and
  written around this radio — its own comments cite timing measured on "the
  Baofeng BF-888S model", and the rest of the clone family (Arcshell, Greaval,
  Ansoko, Tenway) rides on it as aliases. So the radio id keeps the name on the
  case and `chirp.model` records what the driver reports, the same split as
  `yaesu_ft277` reporting as a VX-177. That gate earns its keep here: the H-777
  family has a dozen registered variants on one protocol, and the BF-1901
  siblings tune to 520 MHz. The band is the W31E treatment — a single
  400-470 MHz transmit span, which is the published figure and is tighter than
  the driver's 400-490 MHz catch-all — because the FCC grant that applies has
  not been read off this unit; the BF-888S has shipped under more than one FCC
  ID by batch, and reading the label can only narrow this band, never widen it.
  Bench-confirmed on 2026-09-14 by a read-only detect and read: the radio
  reports `Baofeng BF-888` and clones 16 nameless slots. It came back holding
  seven GMRS channels, which is not the factory codeplug it first looked like
  — slots 1-6 match `chioff_bf888s_shared` field for field, down to CTCSS
  141.3 on ChiO ROAD alone — so the radio had already been programmed with an
  earlier revision of the shared profile, and what it holds is operator choice
  rather than evidence about the band.
- Baofeng W31E (`baofeng_w31e`): 16 channels, no display and no channel-name
  field, generic CHIRP path plus headless `chirp-writer` programming. The id
  comes from a `chirp-writer detect` clone rather than from the case, which
  says only `W31`: the radio reports `Baofeng W31E`, and CHIRP carries two
  unrelated W31 drivers — this analog Retevis RT22 variant, and a 30-channel
  `W31D` with a proprietary digital mode and a different codeplug. The
  reported-model gate is what keeps one from being written as the other.
  Unlike the BF-C50 and the Yaesus, its band is a single 400-470 MHz transmit
  span with nothing receive-only: those radios are narrowed by an equipment
  approval record (a CMIIT amateur-service approval, type acceptance), and
  this unit's label carries no FCC ID, no CMIIT ID and no rated power, so
  there is no equipment-side fact to narrow it with. Which slice of that span
  an operator may key up on is licence and jurisdiction policy, and policy
  belongs in profiles — codeplugger is not a ham-only tool, and a
  `capabilities.json` should not encode one radio service's band plan.
- Headless analog writes: a gated `chirp-writer` backend
  (`backends/chirp.py`) mirroring the `dmrconf` / `p64tool` safety pattern —
  explicit confirmation, a mandatory pre-write backup that doubles as the
  detected-radio identity gate, a required read-back diff, and operation-log
  auditing. CHIRP is GPL-3 and codeplugger is Apache-2.0, so CHIRP is reached
  as a subprocess, never an import: the helper lives in the separate GPL-3
  [`chirp-writer`](https://github.com/Chicago-Offline/chirp-writer) repository
  and writes a JSON result the backend gates on. CHIRP's own `chirpc` cannot
  do this job — it opens the port without applying the driver's `BAUD_RATE`
  (only the GUI does, which is why a 115200-baud C64 never answers it) and
  has no CSV-import path at all. Each radio's `capabilities.json` carries a
  `chirp` block binding it to a driver, the `Vendor Model` string that driver
  reports, and whether the radio needs an operator to put it into clone mode
  for every transfer; `clone_mode: manual` (the Yaesu ft7800 family) makes the
  backend run the helper with the terminal attached instead of capturing it.
  **First hardware verification landed 2026-09-14 on the FT-2800M**, a
  `clone_mode: manual` radio: a headless write was confirmed in both
  directions with a deliberately distinguishable payload each way, checked on
  the radio's own display and corroborated by each run's pre-write backup
  capturing exactly what the previous run left. A headless read separately
  reproduced an intended codeplug with zero mismatches. Remaining acceptance
  work is the on-demand path — re-running the two C64 writes through the
  backend rather than the one-off script.
- Benshi Bluetooth radios (`vero_vrn76`, `btech_uv_pro`): a region-plan JSON
  export (`exporters/benlink_plan.py`) and a gated write backend
  (`backends/benlink.py`) built on
  [`benlink`](https://github.com/khusmann/benlink). These radios have no
  programming cable and no CHIRP driver, and there is no prospect of one:
  CHIRP has no Bluetooth transport at all, so the vendor phone app and
  benlink's reverse-engineered BLE/RFCOMM protocol are the only ways in.
  benlink is Apache-2.0, the same licence as codeplugger, so unlike the GPL-3
  CHIRP path it is imported rather than subprocessed — as an optional extra,
  so codeplugger stays installable without a Bluetooth stack. Two things about
  these radios do not fit the shape every other target has. Channels are
  region-scoped: each region (a "group" on the VR-N76, a "bank" on the
  UV-Pro) owns its own channel table instead of indexing one global pool, so
  a resolved zone becomes a region and a channel wanted in two zones occupies
  a slot in both. And the channel record carries a `tx_disable` bit, so a
  receive-only assignment is finally exported as receive-only rather than as
  the plain simplex channel the CHIRP CSV path has to settle for. The safety
  model differs to match: there is no codeplug image to diff, each channel is
  its own round trip, so the mandatory backup is a JSON snapshot of every
  region the plan will touch and slots the plan does not fill are erased
  rather than left behind (a region may opt out with `blank_unlisted: false`,
  which is how the N76's firmware-owned APRS region stays untouched). There is
  no model string to gate on either. Where a `product_id` has been read off a
  physical radio the backend refuses to write to anything else; where it has
  not, the fallback is the connected radio's self-reported region and channel
  counts — which differ across the family, 6x32 on the VR-N76 against the
  UV-Pro's advertised 180 channels in six banks — and any model not marked
  `hardware_verified` needs an explicit opt-in regardless. Region support
  (read/write region names and per-region channels) is not in an upstream
  benlink release yet; the dependency is pinned to the Chicago Offline fork
  while khusmann/benlink#28 is in draft. The VR-N76's bands get the W31E
  treatment — 136-174 and 400-470 MHz transmit, per the case marking and the
  vendor, with the 108-136 MHz aviation band receive-only — because FCC
  Part 97 is not an equipment-certification regime and no grant exists to
  narrow them with. The UV-Pro entry omits `bands` entirely rather than
  guessing one: BTECH publishes no transmit span, and an invented one is
  exactly the inference this project refuses. Neither radio has had a
  codeplugger-generated plan written to it yet.
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

## Now: publish community codeplugs, get off fork pins, finish analog acceptance

Every stage of the pipeline works and four write backends exist, but the
only operator who has ever run it is the maintainer, on the bench. The
priority now is getting the output into other people's hands with nothing
to install, and making the ecosystem the project depends on outlast a
single maintainer's forks:

1. **Serve prebuilt community codeplugs from the site.** **Landed
   2026-09-21 for the DM-32.** The Pages workflow checks out `ssrf-lite` at
   the revision `pyproject.toml` pins the package to, plus
   `chioff-ssrf-shared` and `chioff-codeplugger-profiles-shared`, and
   `tools/build_site_artifacts.py` resolves every profile listed in
   `site/codeplug-sources.json` and publishes per profile: the export files
   the radio's support-table row promises, the HTML reference, the resolved
   radio-neutral codeplug, and a `SHA256SUMS`. Downloads are linked from
   `site/codeplugs/`, generated fresh on every build and never committed.
   Which formats a profile produces is derived from the radio's row in
   `site/radios.json`, so the table cannot advertise a CHIRP CSV column and
   then fail to offer the file; a radio whose only export needs a device
   read (the P4's `p64tool decode` baseline) fails the build rather than
   publishing nothing. The same job is the end-to-end integration test the
   project has lacked: every SSRF or profile change rebuilds on CI, a change
   like the September GMRS wide/narrow flip shows up as an artifact diff
   rather than a bench surprise, and the published hashes make the
   deterministic-build claim something anyone can check locally. Shared
   profiles are instance-agnostic, so no personal DMR IDs are involved
   (qdmr output carries the `UNUSED` placeholder ID). Remaining: add the
   other shared profiles (BF-888S, then the P4 once it has a
   source-buildable export), give the generated radio table a column
   linking each radio to its codeplug, and have the source repositories
   trigger a rebuild on push instead of relying on the nightly run.
2. **Upstream the forks the project is pinned to.** Three write paths
   currently depend on code that lives only in a personal fork or a local
   checkout, each a single-maintainer bus factor:
   - **benlink**: region (channel bank) read/write is pinned to the
     `emuehlstein/benlink@n76-support` branch while khusmann/benlink#28 is
     in draft. Land it, then move the `benlink` extra to a release.
   - **qdmr**: the DM-32UV button mappings and display colors are
     hardware-validated in a local fork but not upstream. The work is a
     `DM32UVExtension` in the pattern of qdmr's AnyTone/Radioddity/TYT
     extensions, wired into `GeneralSettingsElement::decode/encode`, plus
     `dmrconf verify` limits and docs. One PR each, smallest first:
     display colors, then programmable key functions (SK1/SK2/P1/P2 short
     and long press, long-press duration, side-key lock), then the
     remaining general settings NeonPlug exposes (backlight, date format,
     roger tone, APO), then per-channel extras (key index). Each PR is
     validated against a real radio via read/write round-trip. On the
     codeplugger side, expose each landed setting through the existing
     `extensions.qdmr` passthrough first; promote something to first-class
     profile schema only if it turns out to be radio-neutral policy
     (button mappings are the likely candidate). NeonPlug's byte codec and
     the [base-free experiment](neonplug-base-free-experiment.md) are the
     reference for field meanings, not a delivery path. Until a PR is
     released, pin the exporter's `QDMR_CONFIG_VERSION` and tests to the
     qdmr revision that carries it and document the required build.
   - **CHIRP**: the BF-C50 driver exists on the `bf-c50` branch of
     `emuehlstein/chirpnewfangs` but cannot go upstream until an image read
     off a physical radio exists, because CHIRP's driver tests are
     image-driven. Read one, add the test image, open the PR; only then
     does `baofeng_bf_c50` get a `chirp` block.
3. **Finish `chirp-writer` acceptance on an on-demand radio.** The
   backend is hardware-verified on the FT-2800M, a `clone_mode: manual`
   radio, which was the risky half. The other half — a radio that answers
   on demand — has only been written through the one-off C64 script.
   Re-run the two C64 writes through the backend with a clean read-back
   diff, then delete the script. Until then the analog headless claim
   rests on one radio.
4. **Allow the same assignment in more than one zone.** Profile 0.1
   rejects it ("assignment X is selected more than once"). On region-scoped
   radios (the Benshi family) that blocks the natural "same repeater in
   several groups" layout outright, and on every other radio it is an odd
   restriction with no capability behind it. Lift it in the schema and
   let exporters that index a global channel pool emit the channel once.
5. **Model instance-agnostic profiles in the fleet join.** The registry
   join is 1:1 `radio_instance == instance_id`, so every shared community
   profile errors under `codeplugger-fleet` by design and is validated
   per-profile instead. Item 1 sidesteps this by publishing per profile,
   but it is the same gap: a profile with no `radio_instance` should be
   joinable to any registered radio of its `radio`.
6. **Keep the DM-32 fleet running.** Program the real fleet from the
   private profile repository, keep the operation log and read-back
   archives as the audit trail, and feed any on-radio surprises back into
   SSRF data, profiles, or exporter defaults (never into the generated
   codeplug).
7. **More qdmr-supported radios via `capabilities.json` only.** The qdmr
   YAML exporter and `dmrconf` backend are already radio-neutral; adding a
   radio qdmr supports should mostly be a capabilities file plus a
   `dmrconf verify --radio=<key>` skip-if-uninstalled test. Candidates in
   rough order of community demand: AnyTone AT-D868UV/878UV/578UV, TYT
   MD-UV380/UV390 and Retevis RT3S, Radioddity GD-77, BTECH DMR-6X2,
   Baofeng DM-1701. Each needs a named, reviewed set of exporter defaults
   where qdmr's limits differ from the DM-32UV (placeholders, name lengths,
   list minimums).
8. **OpenGD77 via `dmrconf`.** Landed as `radios/retevis_rt3s_opengd77/`
   (OpenGD77 firmware on MD-UV380 hardware, dmrconf key `openuv380`):
   capabilities from qdmr's shared `OpenGD77Limits`, plus a
   `VERIFY_RADIO_KEYS` stand-in in the dmrconf backend because
   `verify --radio=openuv380` segfaults in qdmr 0.15.1 (no-device radio
   construction dereferences the device handle; `opengd77` shares the same
   limits singleton). Scan lists are declared `max_scan_lists: 0` -- OpenGD77
   scans zones and qdmr silently ignores scan lists, so profiles must not
   carry them. An OpenGD77 CPS CSV exporter is still only warranted if the
   `dmrconf` path leaves gaps. Remaining: hardware-verify a write to
   `eam_rt3s_01` and flip the site row's `hw` flag.

Known gaps:

- Only the DM-32 shared profile is published as a download so far; the
  other shared profiles and the per-radio links in the support table are
  the remainder of item 1 above.
- Three write paths depend on unmerged fork branches or local checkouts
  (benlink regions, qdmr DM-32UV extension, CHIRP BF-C50 driver); tracked
  by item 2 above.
- DM-32 display colors, button functions, and most general settings are
  not reachable through a released `dmrconf` today; tracked by item 2.
- The `chirp-writer` backend is hardware-verified on one radio
  (FT-2800M, manual clone mode). The on-demand path has only been driven
  by the one-off C64 script; tracked by item 3.
- The `benlink` backend has not written a codeplugger-generated plan to a
  physical radio yet; `vero_vrn76` stays `hardware_verified: false` until
  it has.
- An assignment cannot appear in two zones; tracked by item 4.
- Shared community profiles cannot participate in the fleet dry run;
  tracked by item 5.
- Scan lists are membership-only (no priority channels) across exporters.

## Later

- Publish to PyPI so `uvx codeplugger-profile` works without a clone.
  Lower priority once the site serves artifacts, since most community
  members will never need the CLI; blocked anyway on the `ssrf-lite` and
  `benlink` dependencies being releases rather than git pins.
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
- Radios outside the qdmr / CHIRP / p64tool / benlink backends already in
  the tree

## Non-goals

- Becoming a CPS GUI or codeplug editor.
- Treating any generated file or radio read as authoritative data.
- Supporting codeplug round-trip editing; the flow is one-way from sources.
