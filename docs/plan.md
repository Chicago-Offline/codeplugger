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

- One command from a boxed radio to a programmed one (`codeplugger`):
  `radios`, `use-cases` and `profiles` list what is supported, `build`
  compiles a use case or a community profile onto a radio, and `program`
  writes it through whichever of the four backends that radio's
  capabilities declare. No profile, no instance registry and no format
  knowledge required, which is the gap between "codeplugger supports your
  radio" and "you can use codeplugger". Nothing here is a second code
  path: the synthesized profile faces the same schema, limit and
  radio-support checks as a hand-written one, and writes go through the
  backends' existing confirm / backup / read-back gates. Which exporter and
  backend a radio uses is derived from its `capabilities.json` blocks
  (`chirp`, `benlink`, `dmrconf`, `p64tool`), so adding a radio stays a
  capabilities file.
- Fitting a use case to a radio does what the committed per-radio example
  profiles do by hand: drop the assignments the radio physically cannot
  use, then whole zones in the order the use case ranks them, until the
  result fits. It reaches the same answers those profiles do (15 channels
  in one zone for a BF-888S GMRS handheld, 26 for a UV-5R Mini marine set),
  but every drop is printed and has to be accepted before a radio is
  touched, and `--zones` takes the decision back.
- Profile sources: `--profile` takes a file, a directory, a git URL, or the
  name of a registered collection (`--radio baofeng_bf888s --profile
  chioff`), resolved from the same `site/codeplug-sources.json` the
  downloads are built from, so anything the site publishes can also be
  programmed directly; `--radio` picks the profile inside the collection,
  and two candidates for one radio is a question rather than a guess. A
  git URL is cloned once into a cache and its profiles are found by content
  (schema 0.1 with a `radio`). Only git's ordinary transports are accepted,
  because `ext::` would let a URL run commands.
- The registry (`site/codeplug-sources.json`) stays a reviewed allowlist --
  being listed is what gets a collection published as downloads -- but it
  is not a gate on use: a group can hand out a URL before anyone reviews
  anything. A repository the registry already knows by URL is treated as
  the registered entry, which is what supplies the SSRF overlay its
  profiles select from; a bare directory with the same name borrows only
  the overlay.
- Profile resolution and validation (`codeplugger-profile`): SSRF roots plus
  overlays, profile schema 0.1, radio capability limits including
  firmware-dependent limits, optional instance-registry join checks,
  severity-graded validation (hint/warning/critical) that reports every
  critical issue in one run. Checks stay service-specific where a general
  rule would misfire: a reversed GMRS repeater pair warns because 47 CFR
  95.1763 fixes those eight pairs, but no offset-direction rule is applied
  to ham bands, where negative offsets and reverse splits are normal.
- Profile policy beyond plain channel lists: RX group lists, scan lists
  (membership only), per-assignment TX contact, profile inheritance
  (`extends`, with `zones_only`/`omit_zones` variants, radio-agnostic
  abstract base profiles, and `--print-merged-profile` for review), and
  namespaced `extensions` at the profile and assignment level for
  exporter-native settings.
- Multi-identity DMR IDs: a registry instance may carry several `dmr_ids`
  (for example a licensed ID and an unregistered simplex ID) with a
  `default_dmr_id`; profiles bind one per zone or per assignment. Both the
  qdmr and P4 exporters emit one radio ID per identity actually used.
- Channel facts derived from SSRF: bandwidth from the ITU emission
  designator, AM mode for airband receive-only assignments.
- Registry-driven fleet dry run (`codeplugger-fleet`): resolves every
  registered radio, validates, and reports deterministic codeplug hashes.
- Retevis MateTalk P4: TOML export against a `p64tool decode` baseline,
  fleet-registry contacts, and a gated `p64tool` write backend with a
  mandatory pre-write backup and read-back verification.
- Baofeng DM-32: qdmr YAML export (`exporters/qdmr_yaml.py`, every default
  named and reviewed; DM-32UV structural minimums satisfied by explicit
  unused placeholders; profile `extensions.qdmr` for qdmr-native settings,
  contacts, and DMR APRS) and a gated `dmrconf` write backend
  (`backends/dmrconf.py`) mirroring the `p64tool` safety pattern:
  read-only `detect`/`read`/`verify`, explicit confirmation, pre-write
  verify gate, detected-radio identity gate, a mandatory pre-write backup
  and post-write read-back (neither path may already exist; the read-back
  is an archived image, not a diff -- dmrconf takes YAML in and returns a
  device image, so there is nothing to compare it against), and
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
- Baofeng DM-1701 (`baofeng_dm1701`, OEM firmware; the Retevis RT84 is the
  same radio under qdmr's alias key `rt84`): the second `dmrconf` target,
  and the first one that was *not* just a capabilities file. Limits come
  from qdmr's `DM1701Limits` (3000 channels, 250 zones, 31-channel scan
  lists — not the DM-32UV's 15 — and a single DMR ID). **Hardware
  acceptance passed 2026-10-08** on `eam_dm1701_01`: detect, binary read,
  a 219-channel / 22-zone write, read-back, decode and diff, with the
  read-back matching exactly and only the targeted bytes differing from
  the factory image. That run also found the exporter bug the DM-32 could
  not: TyT OEM firmware renders the second display line from zone bank B,
  so every channel showed "Unprogram." until the exporter mirrored the zone
  into both banks. That is driven by a new `zone_banks` capability (2 on
  this radio, absent everywhere else) so OpenGD77 targets stay
  byte-identical. The write progress bar lies on this family as on the
  DM-32; the read-back diff is the only judge.
- OpenGD77 via `dmrconf`: `retevis_rt3s_opengd77` (OpenGD77 on MD-UV380
  hardware, dmrconf key `openuv380`) and `baofeng_dm1701_opengd77` (the
  same STM32 build on the DM-1701, so identical limits). Capabilities come
  from qdmr's shared `OpenGD77Limits`: 1024 channels, 68 zones of 80, a
  470 MHz UHF ceiling where the OEM firmware allows 480, and
  `max_scan_lists: 0` because OpenGD77 scans by zone and qdmr silently
  ignores scan lists, so a profile that carries them is rejected loudly
  rather than dropped. The backend carries a `VERIFY_RADIO_KEYS` stand-in
  because `verify --radio=openuv380` segfaults in qdmr 0.15.1. **The RT3S
  was hardware-verified end to end on 2026-09-23** (`eam_rt3s_01`: a
  16-channel GMRS/MURS profile written with `--init-codeplug`, since a
  fresh flash has an undecodable empty zone table, and read back with
  names, zones and DCS tones intact). The DM-1701 OpenGD77 entry is not
  badged: that firmware has never been flashed on the radio here.
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
- Baofeng BF-1909 (`baofeng_bf1909`): the third H-777 family radio, 16
  nameless channels on the BF-888S clone protocol, and the one where id,
  case and CHIRP's `Vendor Model` string finally agree. Bench-confirmed
  2026-10-09 against `eam_1909_01`: a read-only clone returned the driver's
  full 1008-byte image, a generated 16-channel plan chosen to differ from
  the factory content wrote and re-read byte-identical, and the factory
  image was restored and re-read to its original hash. The band is the weak
  claim and is flagged as such: 400-470 MHz is from retail listings, not a
  grant, and the unit shipped on the PMR446 grid but accepted 462 MHz, so
  firmware bounds nothing. It carries the published PMR446 use case.
- Quansheng UV-K5 running F4HWN (`quansheng_uvk5_f4hwn`): a custom-firmware
  radio id, deliberately separate from a stock UV-K5 because custom builds
  change the TX span, channel count and settings surface, and a stock image
  will not load in the custom driver. 200 channels, 10-character names, no
  zones; TX only in 137-174 and 400-470 MHz, every other span receive-only.
  The driver is `armel/uv-k5-chirp-driver`, out of tree and pinned to F4HWN
  v4.3 — CHIRP's bundled `uvk5.py` dispatches on firmware prefix and refuses
  this one — and it imports wx at module level, so headless use needs a wx
  stub. Limits were probed from `get_features()` with the radio on the
  cable on 2026-10-16.
- Paper-derived CHIRP radios: TYT TH-9800 (`tyt_th9800`, 800 channels,
  quad-band 10 m / 6 m / 2 m / 70 cm FM mobile, with a `tyt_th9800_mars`
  extended-TX twin) and Yaesu VX-6R (`yaesu_vx6r`, 900 channels, tri-band
  submersible handheld). Both have 6-character names and both are limits
  read out of CHIRP driver source rather than off a radio; the TH-9800
  driver is an `ExperimentalRadio` whose own prompt tells the operator to
  keep an OEM backup, and anything codeplugger emits for it inherits that
  warning. Neither has been read or written here. The FT-270R also has its
  `yaesu_ft270_mars` twin now, on the same reasoning as the FT-277R.
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

1. **Run `codeplugger program` against a physical radio, once per
   backend.** The README now leads with this command, and it wires the
   same four backends the bench work verified, but the wiring itself (port
   handling, backup and read-back paths, the operation log it opens) has
   only been exercised against stubbed subprocesses. Every hardware result
   recorded above was produced by the backend or helper directly, not by
   this command. Acceptance is one write per backend -- `dmrconf` on a
   DM-32 or DM-1701, `chirp-writer` on the FT-2800M or BF-1909, `p64tool`
   on the P4, `benlink` once item 4 has a radio -- with a clean read-back
   diff and the operation-log entry it produced, before the site tells
   anyone to run it.
2. **Serve prebuilt community codeplugs from the site.** **Landed
   2026-09-21 for the DM-32; every shared profile with a source-buildable
   export is published as of 2026-10-14.** The Pages workflow checks out
   `ssrf-lite` at the revision `pyproject.toml` pins the package to, plus
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
   (qdmr output carries the `UNUSED` placeholder ID). A source may also omit
   `checkout` and ship in this repository, resolving against the pinned
   `ssrf-lite` package with nothing cloned; the `use-cases` source publishes
   the `examples/usecases/` codeplugs (GMRS, PMR446, MURS, marine VHF, and
   the combined set) that way, which is what lets someone buy a supported
   radio and download a file for it. The support table links each radio to
   the codeplugs built for it: `gen_radio_table.py --codeplugs` reads the
   manifest `build_site_artifacts.py` writes, so the deploy runs the builder
   first and a link only exists for a file that exists. The links are a
   deploy-time artifact and never committed, which is why `--check` refuses
   a manifest. The published set is now the `use-cases` source plus the
   DM-32, UV-5R Mini and BF-888S shared profiles; the `use-cases` source
   also publishes for the BF-1909 and the UV-Pro. The UV-Pro download is a
   benlink region plan, derived from the radio's `benlink` capabilities
   block rather than a table column, for a radio whose write path has no
   hardware acceptance yet (item 4): the file is reviewable and
   deterministic, but nobody has yet written one to a radio, and the
   download card says so for any `benlink` radio not marked
   `hardware_verified`.
   Remaining: the
   P4, which stays unpublishable until its export can be built without a
   device read; and the rebuild trigger. The receiving side is done --
   `pages.yml` accepts a `sources-changed` `repository_dispatch` and runs
   nightly -- so what is left lives in the *other* repositories:
   `ssrf-lite`, `chioff-ssrf-shared` and `chioff-codeplugger-profiles-shared`
   each need a workflow that sends the dispatch on push. Until they do, a
   profile or SSRF change waits for the nightly run.
3. **Upstream the forks the project is pinned to.** Three write paths
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
4. **Finish `chirp-writer` acceptance on an on-demand radio, and make the
   badges match the evidence.** The backend is hardware-verified on the
   FT-2800M, a `clone_mode: manual` radio, which was the risky half. The
   other half -- a radio that answers on demand -- has no backend-driven
   write on record. The badges now match the evidence: the BF-1909 round
   trip on 2026-10-09 went through the CHIRP driver directly, so its row
   carries a CHIRP `hw` badge and no headless one; the UV-K5 F4HWN's
   headless badge is dropped, because its notes record only a
   `get_features()` probe, no write and no read-back. Next: run
   `chirp-writer` on the BF-1909 or UV-K5 with a clean read-back diff and
   badge the headless column, and re-run the two C64 writes through the
   backend and retire the one-off script (it lives in
   `retevis-c64-info`, not here). Until then the analog headless claim rests on one
   radio. The `benlink` backend is in the same position with no radio at
   all: nothing codeplugger generated has been written to a VR-N76 or
   UV-Pro, and `vero_vrn76` stays `hardware_verified: false` until it has.
5. **Allow the same assignment in more than one zone.** **Landed
   2026-10-16.** A repeat is the same channel shown in another zone: it
   takes no new channel number and counts once against `max_channels`
   (and in use-case fitting), but against each zone's
   `max_channels_per_zone`. Every occurrence must ask for the same channel
   -- identical per-assignment settings and effective DMR ID -- or the
   profile is rejected; listing one assignment twice in one zone still is.
   Global-pool exporters (CHIRP, qdmr, P4) emit the channel once; the
   benlink plan gives it a slot in each region.
6. **Model instance-agnostic profiles in the fleet join.** The registry
   join is 1:1 `radio_instance == instance_id`, so every shared community
   profile errors under `codeplugger-fleet` by design and is validated
   per-profile instead. Item 2 sidesteps this by publishing per profile,
   but it is the same gap: a profile with no `radio_instance` should be
   joinable to any registered radio of its `radio`.
7. **Keep the DM-32 fleet running.** Program the real fleet from the
   private profile repository, keep the operation log and read-back
   archives as the audit trail, and feed any on-radio surprises back into
   SSRF data, profiles, or exporter defaults (never into the generated
   codeplug).
8. **More qdmr-supported radios via `capabilities.json` only.** The qdmr
   YAML exporter and `dmrconf` backend are radio-neutral, and the DM-1701
   is the test of the claim: it needed a capabilities file, a
   `dmrconf verify` skip-if-uninstalled test, a `RADIO_NAMES` entry
   captured from a real detect -- and one exporter change (`zone_banks`),
   because TyT OEM firmware's two-bank zones were invisible from source
   and only showed up on the radio's display. Expect each new family to
   cost about that much. Candidates in rough order of community demand:
   AnyTone AT-D868UV/878UV/578UV, TYT MD-UV380/UV390 on OEM firmware and
   Retevis RT3S likewise, Radioddity GD-77, BTECH DMR-6X2. Each needs a
   named, reviewed set of exporter defaults where qdmr's limits differ
   from the DM-32UV (placeholders, name lengths, list minimums), and none
   gets an `hw` badge off a `dmrconf verify` pass alone.
9. **OpenGD77 via `dmrconf`.** Landed and hardware-verified on the RT3S
   (see Current state). Remaining: flash `eam_dm1701_01` to OpenGD77 and
   write through `baofeng_dm1701_opengd77` once, so the second entry stops
   being a copy of the first's limits with no radio behind it; and keep
   the `VERIFY_RADIO_KEYS` workaround until a qdmr release fixes the
   no-device `verify` crash. An OpenGD77 CPS CSV exporter is still only
   warranted if the `dmrconf` path leaves gaps.
10. **`codeplugger import`: onboard from a radio the user already likes.**
    The on-ramp still assumes someone starts from SSRF assignment IDs;
    most people start from a radio that is already set up the way they
    want it. `import` reads a radio of a *known* model -- `--radio` is
    required and the backend's `detect` is an identity gate, never a guess
    -- decodes it to the neutral channel shape, and matches every channel
    against SSRF. The matching is the feature, not the read; it sorts into
    three buckets. An exact match (frequency, offset, tone, mode agree)
    becomes an assignment ID in a draft profile, with the radio's zone and
    channel order carried over as ordering policy and any differing label
    as the per-assignment `display_name` that already exists. A near miss
    (frequency agrees, tone/color code/bandwidth/name differs) becomes a
    reconcile report plus a ready-to-PR SSRF overlay fragment -- that is
    the other half of this item, the path for field edits noticed in use
    ("this repeater's color code is not what the directory says") to get
    back into shared data instead of dying in one operator's radio. No
    match becomes a draft overlay assignment carrying its provenance and a
    TODO, referenced by the draft profile so the result resolves.

    This does not make a codeplug a data source. The output is draft
    *source* for human review -- a profile and an overlay fragment -- never
    a round-trip, and the match report states plainly what did not survive
    the read. Guardrails follow the existing patterns: write only into a
    user-named output directory, never into the installed `ssrf-lite` data,
    the registry, or this repository; refuse to overwrite an existing path
    the way the backup and read-back gates do. Privacy matters here in a
    way it does not elsewhere in the pipeline, because a real read carries
    DMR IDs, contact names, and possibly unpublished frequencies: warn on
    it, and keep every committed fixture synthetic.

    Phase it so no hardware risk arrives before the logic is proven.
    Phase 0 is `--image` only, an offline file-in/drafts-out command tested
    against synthetic fixtures, with one backend's decode (CHIRP first --
    widest radio coverage and the simplest neutral form). Phase 1 adds
    `--port` over the reads the backends already expose (`chirp.read`,
    `p64tool.read`, `dmrconf.read`, `benlink.backup`). Phase 2 adds a
    `reconcile` mode that compares a radio against an existing profile and
    emits only the SSRF-shaped differences. The decoders belong in an
    `importers/` package mirroring `exporters/`, since each one is the
    inverse of an exporter that already exists.

Known gaps not tracked by an item above:

- Scan lists are membership-only (no priority channels) across exporters;
  listed under Deferred.
- Several real capabilities (call-sign database size, APRS positioning
  systems, encryption key slots, SSB receive on the UV-K5) have no schema
  field and live only in `notes`. Fine until a check needs one.

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
