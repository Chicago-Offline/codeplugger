# Examples

Runnable starter profiles for one radio of each kind codeplugger exports to.
They select only published, nationally valid assignments from `ssrf-lite`
(2 m / 70 cm calling frequencies, MURS, GMRS, PMR446, US marine VHF, NOAA
weather), so they resolve with nothing more than the installed dependency.

| Radio | Profile | Shows | Export |
|---|---|---|---|
| Baofeng UV-5R Mini | `profiles/baofeng_uv5r_mini/example.yml` | single zone, 12-char names, `display_name` override | CHIRP CSV |
| Baofeng DM-32 | `profiles/baofeng_dm32/example.yml` | several zones, registry-supplied DMR ID | qdmr YAML |
| Baofeng BF-888S | `profiles/baofeng_bf888s/example.yml` | 16 knob positions, no name field | CHIRP CSV |
| BTECH UV-Pro | `profiles/btech_uv_pro/example.yml` | region-scoped zones, 10-char names | benlink plan |

`instances.yml` registers one physical radio per profile. The registry key is
what a profile's `radio_instance` must match; `profile` names the profile ID
that radio should carry.

## Use cases

`usecases/` answers a different question: not "what can this radio do" but
"make the radio I bought behave like the radio I wanted". Each file is an
**abstract base profile** - it names no `radio` and no `radio_instance`, so it
is not programmable on its own - and each is carved out of
`usecases/multiservice.yml` with `zones_only`.

| Use case | Base profile | Zones |
|---|---|---|
| A GMRS handheld | `usecases/gmrs.yml` | GMRS, FRS RX, Weather |
| A MURS handheld | `usecases/murs.yml` | MURS, Weather |
| A PMR446 handheld | `usecases/pmr446.yml` | PMR446 |
| A marine VHF set | `usecases/marine.yml` | Marine A, Marine B, Weather |
| All of them at once | `usecases/multiservice.yml` | all seven |

The quickest way to use one is not to write a profile at all:

```bash
uv run codeplugger build --radio baofeng_bf888s --use-case gmrs
```

That does the fitting below automatically — it drops what the radio cannot
receive, then whole zones in the order the base ranks them — and prints every
drop before it writes anything.

To keep the result, write a profile that names the radio, the registry
instance, and the base to extend:

```yaml
version: "0.1"
id: "my_handheld_gmrs"
name: "My Handheld GMRS"
radio: "baofeng_bf888s"
radio_instance: "my_handheld_01"
extends: "../../usecases/gmrs.yml"
zones_only: ["gmrs"]        # 16 knob positions, so keep the TX channels only
```

That `zones_only` line is the whole point of the split: the base carries more
than any one radio can hold, and each radio drops what it has no room for.
These examples show the range:

| Radio | Profile | Result |
|---|---|---|
| Baofeng BF-888S | `profiles/baofeng_bf888s/gmrs.yml` | 15 channels, 1 zone |
| Baofeng BF-1909 | `profiles/baofeng_bf1909/pmr446.yml` | 16 channels, 1 zone |
| Baofeng UV-5R Mini | `profiles/baofeng_uv5r_mini/marine.yml` | 26 channels, 1 zone |
| BTECH UV-Pro | `profiles/btech_uv_pro/murs.yml` | 12 channels, 2 zones |
| BTECH UV-Pro | `profiles/btech_uv_pro/multiservice.yml` | 77 channels, 5 zones |
| Baofeng DM-32 | `profiles/baofeng_dm32/multiservice.yml` | 100 channels, 7 zones |

Those six are published as ready-to-use downloads under `codeplugs/` on the
[project site](https://chicago-offline.github.io/codeplugger/), rebuilt from
this directory on every change, so nobody has to clone anything to get one.
They are listed in `site/codeplug-sources.json` as the `use-cases` source.

What these profiles do **not** do is grant you authority to transmit. GMRS
needs an FCC licence, marine VHF needs type-accepted equipment that none of
these radios are (which is why the marine zones resolve receive-only), and
PMR446 is licence-exempt in CEPT countries at 0.5 W ERP, not everywhere at any
power. Program the zones you are allowed to use.

## Run them

From the repository root:

```bash
uv run codeplugger-profile examples/profiles/baofeng_uv5r_mini/example.yml \
  --instance-registry examples/instances.yml \
  --output-format chirp-csv

uv run codeplugger-profile examples/profiles/baofeng_dm32/example.yml \
  --instance-registry examples/instances.yml \
  --output-format qdmr-yaml

uv run codeplugger-profile examples/profiles/btech_uv_pro/example.yml \
  --instance-registry examples/instances.yml \
  --output-format benlink-plan

uv run codeplugger-fleet \
  --instance-registry examples/instances.yml \
  --profiles-root examples/profiles
```

Add `--output-format html` to any `codeplugger-profile` call for a printable
reference, or `--artifact-root .` to write it under `.artifacts/`.

## Make them yours

Profiles for real radios live in a repository you own, not in codeplugger.
That keeps your radio inventory, zone layouts, and DMR IDs under your control
and lets you update codeplugger and `ssrf-lite` independently of them.

1. Copy this directory somewhere outside the codeplugger checkout:

   ```bash
   cp -r examples ../my-codeplugger-profiles
   cd ../my-codeplugger-profiles
   git init
   ```

2. In `instances.yml`, replace the example entries with your radios. Keep one
   entry per physical radio and give each a stable key such as `uv5r_01`. Put
   your real DMR ID in `dmr_id`; the `1234567` placeholder must not be
   transmitted with.

3. Rename the profiles, set each `radio_instance` to the matching registry key
   and `id` to the registry's `profile` value, and change the assignments.
   Assignment IDs come from `ssrf-lite`; browse
   `plans/` and `systems/` in that repository to find channels near you.

4. Validate from the codeplugger checkout, pointing at your files:

   ```bash
   uv run codeplugger-fleet \
     --instance-registry ../my-codeplugger-profiles/instances.yml \
     --profiles-root ../my-codeplugger-profiles/profiles
   ```

Channels that `ssrf-lite` does not have yet belong in an SSRF overlay
repository rather than in a profile. Overlays are layered with explicit roots
in precedence order, which replace the installed default:

```bash
uv run codeplugger-fleet \
  --instance-registry ../my-codeplugger-profiles/instances.yml \
  --profiles-root ../my-codeplugger-profiles/profiles \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../my-ssrf-overlay/ssrf
```

Profiles choose and order channels; they do not define frequencies.
