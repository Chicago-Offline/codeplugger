# NeonPlug Base-Free Import Experiment

Date: 2026-08-05

NeonPlug revision tested:
`456c4bf3d5155d966e9291570b9633df35731748`

## Question

Can Codeplugger generate a minimal analog channel and zone without copying an
existing `.neonplug` file?

## Method

A temporary Vitest probe exercised NeonPlug's own implementation:

- `smartImportCodeplug()` imported a ZIP containing only `codeplug.json`.
- `validateChannelForEncoding()` checked the imported channel model.
- `encodeChannel()` encoded the channel to the DM-32UV 48-byte record.
- `encodeZone()` encoded the zone to the DM-32UV 145-byte record.

The archive omitted an existing codeplug, radio settings, radio information,
contacts, scan lists, and all other unrelated collections.

Two channel payloads were tested:

1. A sparse analog channel containing only its number, name, frequencies,
   mode, and TX prohibition.
2. The same channel completed with NeonPlug's `createDefaultChannel()` helper.

## Results

Both payloads imported without errors. NeonPlug reported the sparse channel as
valid even though `validateChannelForEncoding()` rejected it because
`bandwidth` was absent. This establishes that import success alone does not
show that a channel can be written to the radio.

The source-defaulted channel passed `validateChannelForEncoding()`, encoded to
48 bytes, and its zone encoded to 145 bytes. Missing top-level collections
became empty arrays or `null` as implemented by NeonPlug's importer.

## Conclusion

A personal or device-derived base codeplug is not required at the NeonPlug
import and DM-32UV channel/zone encoding boundary. A complete channel object is
required, and NeonPlug's importer does not fill or validate its channel fields.

NeonPlug's `createDefaultChannel()` is evidence for candidate defaults, not a
radio specification. Codeplugger must name and review each exporter default,
especially power, bandwidth, squelch, step frequency, and unknown fields,
rather than copying the helper wholesale without explanation.

## Remaining Validation

This experiment did not write to a radio. Before considering base-free output
safe, generate a synthetic `.neonplug`, inspect the imported values in the
pinned NeonPlug revision, and perform a DM-32UV hardware smoke test.
