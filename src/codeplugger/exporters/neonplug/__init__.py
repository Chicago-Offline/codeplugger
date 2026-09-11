"""Export resolved analog codeplugs to NeonPlug's ``.neonplug`` format.

Profile 0.1 scope: analog FM channels and zones only (``ResolvedCodeplug``
mode ``FM``, tx/rx frequency, TX prohibition, CTCSS/DCS tones, power,
bandwidth). Digital/DMR channels, scan lists, contacts, DMR identities, and
button settings are out of scope and raise an actionable error rather than
being invented or silently dropped -- see :mod:`.writer`.

Evidence and discovery-gate findings (issue #4)
------------------------------------------------
1. **NeonPlug source.** Pinned revision ``infamy/NeonPlug`` @
   ``8ae184770e03a93959f81c262f2ba9dcb93b0400`` (MIT licensed). Relevant
   files: ``src/models/Channel.ts``, ``src/models/Zone.ts``,
   ``src/services/codeplugExport.ts`` (import/export + ``CodeplugData``),
   ``src/utils/channelHelpers.ts`` (``createDefaultChannel``,
   ``validateChannelForEncoding``), ``src/radios/dm32uv/structures.ts``
   (``encodeChannel``/``parseChannel``/``encodeZone``/``parseZones``,
   the actual DM-32UV byte codec), ``src/radios/dm32uv/constants.ts``
   (``LIMITS``). This repo previously ran a similar probe against an
   earlier NeonPlug revision -- see
   ``docs/neonplug-base-free-experiment.md`` -- whose findings this
   implementation confirms still hold on the current pinned commit.

2. **Base-free generation: confirmed.** ``jsonSafeToCodeplug()`` in
   ``codeplugExport.ts`` defaults every top-level ``CodeplugData`` field
   (``channels``, ``zones``, ``scanLists``, ``contacts``, ``radioSettings``,
   ``radioInfo``, ...) with ``?? []`` / ``?? null`` / ``?? new Date()``.
   A ``codeplug.json`` containing only ``{"version", "channels", "zones"}``
   parses without error and without any base/template export. No minimal
   base-template contract is needed or used.

3. **Fields required for a safe analog write.** NeonPlug's own
   ``validateChannelForEncoding()`` (``channelHelpers.ts``) is the
   authoritative list: ``number``, ``name``, ``rxFrequency``,
   ``txFrequency``, ``mode``, ``bandwidth``, ``power``, ``rxCtcssDcs``,
   ``txCtcssDcs``, ``rxSquelchMode``, ``signalingType``, ``pttIdType``.
   This exporter supplies all of them: the first eight from
   ``ResolvedChannel``/policy, the last three from NeonPlug's own
   "new channel" defaults (below), since they're out of profile-0.1 scope.

4. **Defaults for absent fields, and where NeonPlug defines them.** Every
   channel field this exporter does not own (VOX, compander, squelch level,
   PTT-ID, APRS, emergency signaling, scan-list membership, DMR contact
   routing, and the several unlabeled reserved bitfields) is copied
   verbatim from NeonPlug's own ``createDefaultChannel()`` factory -- the
   same function NeonPlug's UI calls when a human adds a new channel with
   no prior data. See ``channel_defaults.py`` for the full ported table and
   citation. Nothing is invented and nothing is copied from a personal or
   device-specific export.

5. **Verification against the real DM-32UV codec (not just JSON parsing).**
   Passing NeonPlug's own JSON import is necessary but not sufficient --
   ``jsonSafeToCodeplug`` performs no per-channel validation, so a
   structurally-wrong channel would still "import". The stronger,
   evidence-based check is round-tripping generated channels/zones through
   NeonPlug's actual DM-32UV byte codec (``encodeChannel`` -> ``parseChannel``,
   ``encodeZone`` -> ``parseZones``) from the pinned commit above, confirming
   RX/TX frequency, TX prohibition, and CTCSS/DCS tones survive unchanged.
   This was run manually against a synthetic fixture as part of this PR
   (see the PR description for the harness and output); it is not checked
   into this repository because it requires a local NeonPlug checkout and
   ``npm``/``node``, which this public repo's test fixtures must not assume.
   ``tests/test_neonplug.py`` documents the exact commands to reproduce it.

6. **Hardware behavior.** Not evaluated here -- no physical DM-32UV is
   available in this environment. See the PR description: hardware
   smoke-testing is an explicit follow-up for a human with a radio.

7. **Old private DM-32 generator.** ``muehlstein-ssrf-private``'s
   ``radios/dm32/`` codec was consulted only as a source of hypotheses
   (e.g. frequency units, CTCSS/DCS representation) and every hypothesis it
   suggested was independently confirmed against NeonPlug's own source
   above before being used; nothing was ported from it directly, so no
   attribution is owed for this exporter's code.

Power/bandwidth policy
-----------------------
NeonPlug's Channel model only exposes three power levels (``Low`` /
``Medium`` / ``High``) and two bandwidths (``12.5kHz`` / ``25kHz``); it never
converts watts or kHz itself; a human always picks the enum value directly
in NeonPlug's UI. This exporter's watt-to-level thresholds are therefore its
own explicit, reviewed policy (see ``writer.py``), not a value inherited
from a copied device export, and mirror this repo's ``qdmr_yaml`` exporter
for consistency. Bandwidth has no default: it must come from
``ResolvedChannel.bandwidth_khz`` or the explicit ``analog_bandwidth_khz``
caller argument, and is rejected outright if neither is set.

Out of scope / non-goals
--------------------------
Scan lists, contacts, DMR identities, digital channels, and button settings
are not emitted. This exporter does not decode existing ``.neonplug`` files,
does not attempt byte-identical round trips, and does not preserve unknown
fields from an existing export -- it only ever builds a fresh document from
a ``ResolvedCodeplug``.
"""

from .channel_defaults import (
    FORMAT_VERSION,
    NEONPLUG_PINNED_COMMIT,
    NEONPLUG_SOURCE_REPO,
)
from .writer import neonplug_document_from_resolved, write_neonplug

__all__ = [
    "write_neonplug",
    "neonplug_document_from_resolved",
    "FORMAT_VERSION",
    "NEONPLUG_PINNED_COMMIT",
    "NEONPLUG_SOURCE_REPO",
]
