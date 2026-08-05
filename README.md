# codeplugger

`codeplugger` builds radio codeplugs from shared RF data and user-owned
configuration. Its initial target is a neonplug-compatible import for the
Baofeng DM-32, with room to support other radios and CPS tools later.

## Proposed workflow

```mermaid
flowchart LR
    public["ssrf-lite<br/>Authoritative public RF data"]
    private["user-ssrf-private<br/>Private RF additions and overrides"]
    profiles["user-codeplugger-profiles<br/>Radio profiles and preferences"]
    merge["Resolve and validate inputs"]
    codeplugger["codeplugger<br/>Generate CPS import"]
    cps["neonplug / DM32 CPS"]
    radio["DM32 radio"]

    public --> merge
    private --> merge
    profiles --> codeplugger
    merge --> codeplugger
    codeplugger --> cps
    cps --> radio
```

The diagram source is also available in [docs/workflow.mmd](docs/workflow.mmd).

## Repository responsibilities

- **`ssrf-lite`** is the authoritative source for shareable RF facts: channel
  plans, repeaters, stations, talkgroups, and their schema.
- **`username-ssrf-private`** contains private RF records and explicit
  overrides of `ssrf-lite`, such as local names, unpublished channels, or
  user-specific grouping metadata.
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

## Do profiles need a separate repository?

Eventually, yes. RF data describes what exists; a profile describes what a
particular person wants programmed into a particular radio. Keeping those
concerns separate makes profiles reusable across SSRF dataset updates and
avoids mixing device settings into an RF schema.

A separate profiles repository does not need to be created immediately. Until
the profile format stabilizes, sample profiles can live in this repository and
real private profiles can live in the user's private SSRF repository. Split
them into `username-codeplugger-profiles` once there is a useful schema and a
working generator to exercise it.

## Initial scope

The first end-to-end milestone is intentionally narrow:

1. Read `ssrf-lite` plus optional private overrides.
2. Read one DM-32 profile.
3. Validate channel and radio constraints.
4. Produce an import accepted by neonplug.

The directories in this repository are intended to evolve along these lines:

- `radios/`: radio capabilities, constraints, and exporter-specific mappings.
- `profiles/`: public examples and fixtures, not real user secrets.
