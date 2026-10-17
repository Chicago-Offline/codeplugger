# Operations: artifacts, logs, and the fleet dry run

## Radio artifacts and operation logs

Resolved profiles can produce a self-contained, printable HTML reference and a
JSON Lines operation log under the profiles repository. The artifact format is
radio-independent, so it works for DM-32, P4, and other resolved radio models:

```bash
uv run codeplugger-profile path/to/profile.yml \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../chioff-ssrf-test/ssrf \
  --output-format html \
  --artifact-root path/to/profiles
```

This creates `.artifacts/<radio>/<radio-instance>/reference.html` and
`operations.jsonl`. The latter records successful profile generation and can
also be passed to a backend as an `ArtifactStore` to audit device reads and
writes; `codeplugger program --artifact-root` does this, and the backups and
read-back images it takes land in the same directory. Generated artifacts are
evidence of an operation, not an authoritative profile or RF data source.

The reference begins with the radio model, instance identity, UTC generation
time, and channel, zone, and contact counts. It then lists channels by numbered
zone followed by the DMR contact table. When `--instance-registry` is supplied,
the heading uses the instance's DMR ID and color metadata and the contact table
includes every registry instance that has a DMR ID.

## Registry-driven fleet dry run

The physical fleet registry is the source of truth for which radio instance
gets which profile. A registry entry's `profile` value names a profile file
under the profile repository. A profile that declares `radio_instance` must
name that registry key; a profile that declares none is shared, and the fleet
binds it to each registered radio whose `profile` names it, so one community
profile can serve several radios of the same model with their own DMR IDs. No
radio is changed by this command:

```bash
uv run codeplugger-fleet \
  --instance-registry ../muehlstein-codeplugger-profiles/instances.yml \
  --profiles-root ../muehlstein-codeplugger-profiles/profiles \
  --ssrf-root ../ssrf-lite/ssrf \
  --ssrf-root ../muehlstein-ssrf-private/ssrf
```

The command resolves every registered radio, validates its profile against the
radio capabilities, and reports a deterministic SHA-256 of the resolved
codeplug. A nonzero exit status means at least one instance is not ready; this
is the planning gate before a programming adapter is invoked.

The registry format is described in
[profiles.md](profiles.md#instance-registry).
