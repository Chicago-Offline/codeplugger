# Profiles

A profile selects ordered SSRF assignments by stable ID and groups them into
ordered zones for one target radio. This page covers the file format,
inheritance, how SSRF data is located and layered, per-assignment exporter
extensions, and the optional instance registry. Starter profiles live in
[../examples/](../examples/README.md).

## Profile format

Profile version `0.1`:

```yaml
$schema: "https://raw.githubusercontent.com/Chicago-Offline/codeplugger/main/schemas/profile-0.1.schema.json"
version: "0.1"
id: "dm32_reference"
name: "DM-32 reference"
radio: "baofeng_dm32"
radio_instance: "dm32_reference_01"
zones:
  - id: "reference"
    name: "Reference"
    assignments:
      - "asg_fixture_reference_simplex"
      - "asg_wx1"
```

`radio` identifies the radio model and `radio_instance` identifies the specific
physical radio receiving the generated codeplug. The instance ID is arbitrary
but stable, such as `dm32_green_01` or `d01`. Profiles that omit it retain
backward compatibility by using their profile `id` as the instance ID.

Profiles contain selection and ordering policy, not RF facts or per-user DMR
identities. Display-name or RF changes belong in an SSRF overlay. Version `0.1`
intentionally excludes selectors, scan lists, contacts, button mappings, and
exporter settings until the explicit-ID workflow is proven end to end.

An assignment may appear in more than one zone. A repeat is the same channel
shown in another zone: it takes no new channel number and counts once against
the radio's channel limit, but against each zone's per-zone limit. Every
occurrence must ask for the same channel (identical per-assignment settings),
and a repeat inside a single zone is rejected.

## Inheritance

Profiles may inherit from another profile in the same directory or a relative
path. A parent that names a `radio` constrains the child to that radio; a
parent that names none is an abstract base, reusable on any radio that can
hold it, and the child supplies both `radio` and `radio_instance`. The child's
`id` and optional `radio_instance` are never inherited. Scalar values in the
child replace the parent. Lists of objects with an `id` are merged by ID, with
child fields patching matching objects and new objects appended. Use
`zones_only` or `omit_zones` for a child variant that keeps only part of the
parent's zones:

```yaml
version: "0.1"
id: "dm32_lite"
name: "DM-32 lite"
radio: "baofeng_dm32"
radio_instance: "dm32_white_01"
extends: "dm32_base.yml"
zones_only: ["reference"]
```

`examples/usecases/` uses abstract bases to describe a radio by what it is for
-- a GMRS handheld, a MURS handheld, a PMR446 handheld, a marine VHF set, or
all of them at once -- so the same zone layout can be programmed onto any
radio with the capacity for it.

Inheritance cycles, unknown zones, mismatched radios, and excessively deep
inheritance chains are rejected. Review the fully merged profile with:

```bash
uv run codeplugger-profile path/to/profile.yml --print-merged-profile
```

## SSRF roots

Validate a profile against authoritative data and radio limits. With no
`--ssrf-root`, codeplugger reads the `ssrf-lite` data shipped inside the
installed dependency, so a plain install is enough for profiles that only
select published assignments:

```bash
uv run codeplugger-profile path/to/profile.yml
```

Pass `--ssrf-root` explicitly, in precedence order, to use a local `ssrf-lite`
checkout or to layer overlays on top of it. Explicit roots replace the
installed default rather than extending it:

```bash
uv run codeplugger-profile path/to/profile.yml \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../chioff-ssrf-test/ssrf
```

A root can pin its own place in the merge with a `_root.yml` declaration, in
which case `--ssrf-root` order no longer matters for it (higher precedence
wins; duplicate values are rejected):

```yaml
# ../chioff-ssrf-test/ssrf/_root.yml
ssrf_root:
  id: chioff_test_overlay
  precedence: 100
```

A profile collection passed to `--profile` as a git URL or directory may ship
SSRF data of its own under `ssrf/`; it is used as an overlay automatically.

## The resolved codeplug

Inspect the normalized, exporter-neutral codeplug as deterministic YAML or
JSON:

```bash
uv run codeplugger-profile path/to/profile.yml \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../chioff-ssrf-test/ssrf \
  --output-format yaml
```

The normalized model contains ordered channels and zones, radio-facing RX/TX
frequencies, mode, service, analog tones, overlay-resolved notes, and explicit
TX permission. It intentionally excludes scan lists, contacts, DMR identities,
button settings, and NeonPlug fields; exporters translate this stable model
without participating in input resolution.

## Exporter extensions

Settings that only one exporter understands go under the profile's
`extensions.<exporter>` namespace, either at the top level or on an
assignment. Every other exporter ignores them. Codeplugger rejects assignment
extensions that try to replace generated RF fields.

### Excluding channels from scan (CHIRP)

Analog radios have a single global scan and express membership negatively, per
memory, so there is no scan list to join. Set CHIRP's `Skip` value through an
assignment's `extensions.chirp` namespace:

```yaml
zones:
  - id: main
    name: Main
    assignments:
      - {id: asg_wx1, display_name: "WX 1", extensions: {chirp: {skip: "S"}}}
      - {id: us_ham_vhf_call, display_name: "2MCALL", extensions: {chirp: {skip: "P"}}}
```

`skip` accepts only `""` (scan normally), `"S"` (skip) and `"P"` (priority) —
`chirp_common`'s three values. Individual drivers narrow that set further in
`valid_skips`, which the CSV exporter cannot see, so a value a radio does not
support is caught at import rather than at export.

### qdmr settings and DMR APRS (DM-32)

The qdmr YAML exporter accepts qdmr-native settings and DMR APRS records under
`extensions.qdmr`. Assignment-level qdmr extensions add fields to that
generated channel. For example:

```yaml
extensions:
  qdmr:
    settings:
      introLine1: K9ABC
      boot: {display: Text}
      audio: {fmMicGain: 2, voxDelay: 500 ms}
      dmr: {groupCallMatch: false}
      gnss: {systems: [GPS]}
    contacts:
      - dmr:
          id: aprs_contact
          name: DMR APRS
          ring: false
          type: PrivateCall
          number: 310999
    positioning:
      - dmr:
          id: aprs1
          name: DMR APRS
          period: 5 min
          contact: aprs_contact
zones:
  - id: local
    name: Local
    assignments:
      - id: local_dmr
        extensions:
          qdmr: {aprs: aprs1}
```

Values use qdmr's extensible YAML names and syntax. IDs supplied in
`contacts` and `positioning` must be unique within the generated document.
qdmr currently maps DM-32 boot, audio/VOX, tone, timeout, GNSS, DMR, SMS,
password, and DMR APRS settings. It knows the binary locations of the DM-32
screen colors, brightness, and backlight duration but does not expose them
through its YAML configuration model, so codeplugger cannot set them yet.

## Instance registry

Profiles may declare `radio_instance`, and codeplugger can optionally validate
that identifier against a separate instance registry document:

```yaml
version: "0.1"
instances:
  dm32_green_01:
    radio: baofeng_dm32
    label: "Green DM-32"
    firmware: "DM32.01.L01.048"
```

Use `--instance-registry` to enable this join check:

```bash
uv run codeplugger-profile path/to/profile.yml \
  --instance-registry path/to/instances.yml
```

When enabled, codeplugger verifies:

- the profile instance ID exists in the registry
- the registry entry's `radio` matches the profile's `radio`

A profile with no `radio_instance` can be bound to one at build time, which is
how a shared profile or a use case picks up a particular radio's DMR ID and
firmware limits:

```bash
uv run codeplugger build --radio baofeng_dm32 --use-case gmrs \
  --instance-registry path/to/instances.yml --instance dm32_green_01
```

A profile that already names a different `radio_instance` is rejected rather
than rebound.

If no registry is provided, behavior stays backward compatible with existing
profile-only workflows. The registry is also what drives the fleet dry run;
see [operations.md](operations.md).
