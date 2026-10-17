# Architecture

How the inputs to a codeplug are divided between repositories, and the order
in which they are merged. For the intent behind the split and the roadmap, see
[plan.md](plan.md).

## Repository responsibilities

- **`ssrf-lite`** is the authoritative source for shareable RF facts: channel
  plans, repeaters, stations, talkgroups, and their schema.
- **[`chioff-ssrf-test`](https://github.com/Chicago-Offline/chioff-ssrf-test)**
  is a public reference overlay. It models the structure and merge behavior of
  a private SSRF repository using only public or synthetic fixture data.
- **[`chioff-ssrf-shared`](https://github.com/Chicago-Offline/chioff-ssrf-shared)**
  holds real Chicago Offline community-net RF facts (shared GMRS and MURS
  channels) that members merge on top of `ssrf-lite`.
- **`username-ssrf-private`** contains private RF records and explicit
  overrides of `ssrf-lite`, such as local names, unpublished channels, or
  user-specific grouping metadata.
- **[`chioff-codeplugger-profiles-test`](https://github.com/Chicago-Offline/chioff-codeplugger-profiles-test)**
  contains public reference profiles for Chicago Offline radios and provides
  realistic inputs for end-to-end integration tests.
- **[`chioff-codeplugger-profiles-shared`](https://github.com/Chicago-Offline/chioff-codeplugger-profiles-shared)**
  contains profiles selecting the shared community net for supported radios.
- **`username-codeplugger-profiles`** contains radio and output preferences:
  channel selection, zones, scan lists, button assignments, display settings,
  and per-radio variants.
- **`codeplugger`** resolves those inputs, validates them against radio limits,
  and generates files importable by a supported CPS tool.
- **`dm32-info`** remains a research and reference repository. Stable findings
  needed for generation should become radio definitions or validation rules in
  `codeplugger`, rather than making generation depend directly on research
  notes.

## Precedence

Inputs should be merged deterministically:

1. Load RF records from `ssrf-lite`.
2. Add or override RF records from `username-ssrf-private` using stable record
   identifiers, not display names.
3. Apply a selected codeplug profile to choose and arrange records and set
   radio-specific preferences.
4. Validate the result against the target radio and exporter constraints.
5. Generate the CPS import as a disposable build artifact.

Generated codeplugs should not become an authoritative data source. Changes
should flow back into SSRF data or the selected profile and then be regenerated.

## Reference and user repositories

RF data describes what exists; a profile describes what a person wants
programmed into a particular radio. Keeping those concerns separate makes
profiles reusable across SSRF dataset updates and avoids mixing device
settings into an RF schema.

The two `chioff-*` repositories form a public reference configuration for
integration testing:

```text
ssrf-lite + chioff-ssrf-test + chioff-codeplugger-profiles-test -> codeplugger
```

The `-test` repositories hold only synthetic fixture data and exist to model
the overlay merge behavior. Real user repositories may follow the same layout
while remaining private. Public fixtures must not contain personal identities,
unpublished frequencies, credentials, location-sensitive records, or exports
from real radios.

The `-shared` repositories are the exception: they carry real Chicago Offline
community-net channels that are intended to be published and shared. They
still must not contain personal identities, credentials, or radio exports.

Small synthetic fixtures should remain in this repository for fast,
deterministic unit tests. The `chioff-*` repositories are intended for broader
cross-repository contract and end-to-end tests.

## Directories in this repository

- `radios/`: radio capabilities, constraints, and exporter-specific mappings.
- `examples/`: runnable starter profiles and an instance registry, built only
  from published `ssrf-lite` data. `examples/usecases/` holds radio-agnostic
  base profiles (GMRS, MURS, PMR446, marine VHF, and the combined set) that the
  per-radio profiles bind to hardware. Copy it to start your own profile
  repository; see [../examples/README.md](../examples/README.md).
- `schemas/`: JSON Schema for profiles, capabilities, and the instance
  registry.
- `site/`: the project site, including the registry of community profile
  collections in `codeplug-sources.json`.
- `tests/fixtures/`: synthetic inputs for unit tests, not usable profiles.
