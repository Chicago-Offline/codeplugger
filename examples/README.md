# Examples

Runnable starter profiles for one radio of each kind codeplugger exports to.
They select only published, nationally valid assignments from `ssrf-lite`
(2 m / 70 cm calling frequencies, MURS, GMRS, NOAA weather), so they resolve
with nothing more than the installed dependency.

| Radio | Profile | Shows | Export |
|---|---|---|---|
| Baofeng UV-5R Mini | `profiles/baofeng_uv5r_mini/example.yml` | single zone, 12-char names, `display_name` override | CHIRP CSV |
| Baofeng DM-32 | `profiles/baofeng_dm32/example.yml` | several zones, registry-supplied DMR ID | qdmr YAML |
| Baofeng BF-888S | `profiles/baofeng_bf888s/example.yml` | 16 knob positions, no name field | CHIRP CSV |
| BTECH UV-Pro | `profiles/btech_uv_pro/example.yml` | region-scoped zones, 10-char names | benlink plan |

`instances.yml` registers one physical radio per profile. The registry key is
what a profile's `radio_instance` must match; `profile` names the profile ID
that radio should carry.

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
