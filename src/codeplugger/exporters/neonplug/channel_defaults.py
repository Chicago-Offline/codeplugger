"""Ported NeonPlug channel/format constants for fields outside profile-0.1 scope.

Source (MIT licensed): infamy/NeonPlug
  https://github.com/infamy/NeonPlug @ 8ae184770e03a93959f81c262f2ba9dcb93b0400
  - ``src/utils/channelHelpers.ts``   ``createDefaultChannel()`` /
    ``validateChannelForEncoding()``
  - ``src/services/codeplugExport.ts``  ``CODEPLUG_JSON_FILENAME`` /
    ``CODEPLUG_VERSION`` / ``jsonSafeToCodeplug()``
  - ``src/models/Channel.ts``, ``src/models/Zone.ts`` (field shapes/limits)
  - ``src/radios/dm32uv/constants.ts``  ``LIMITS``

DEFAULT_CHANNEL_FIELDS is copied verbatim from NeonPlug's own "new channel"
factory (``createDefaultChannel()``), which is what NeonPlug's UI itself uses
whenever a human creates a channel with no prior data. Using the same values
means an imported channel looks -- to NeonPlug and to its DM-32UV encoder --
like one a human created by hand and only edited the fields this exporter is
responsible for. Nothing here is invented, and nothing here is copied from a
personal/device codeplug: every value traces back to NeonPlug's own source.

Fields this exporter fills in itself (never taken from here): ``number``,
``name``, ``rxFrequency``, ``txFrequency``, ``forbidTx``, ``power``,
``bandwidth``, ``rxCtcssDcs``, ``txCtcssDcs``. Every other field is out of
scope for profile 0.1 (DMR routing, scan lists, APRS, VOX, signaling, PTT-ID,
squelch mode, contact routing, etc.) and is left at NeonPlug's own "new
channel" value so this exporter never silently invents radio behavior.
"""

from __future__ import annotations

from typing import Any

NEONPLUG_SOURCE_REPO = "https://github.com/infamy/NeonPlug"
NEONPLUG_PINNED_COMMIT = "8ae184770e03a93959f81c262f2ba9dcb93b0400"

# services/codeplugExport.ts: CODEPLUG_JSON_FILENAME / CODEPLUG_VERSION
CODEPLUG_JSON_FILENAME = "codeplug.json"
FORMAT_VERSION = "1.0.0"

# radios/dm32uv/constants.ts LIMITS -- used only to reject codeplugs the
# DM-32UV codec cannot represent, never to silently clamp/truncate data.
CHANNEL_MAX = 4000
ZONES_MAX = 250
ZONE_CHANNELS_MAX = 64

# radios/dm32uv/structures.ts encodeChannel(): name is a 16-byte field.
CHANNEL_NAME_MAX_CHARS = 16
# models/Zone.ts field comment: "Zone name (max 10 chars, written to radio)",
# confirmed by structures.ts encodeZone() which truncates at 10 bytes.
ZONE_NAME_MAX_CHARS = 10

# utils/channelHelpers.ts createDefaultChannel() -- verbatim, see module
# docstring for provenance and scope notes.
DEFAULT_CHANNEL_FIELDS: dict[str, Any] = {
    "loneWorker": False,
    "scanAdd": False,
    "scanListId": 0,
    "forbidTalkaround": False,
    "unknown1A_6_4": 0,
    "unknown1A_3": False,
    "aprsReceive": False,
    "emergencyIndicator": False,
    "emergencyAck": False,
    "emergencySystemId": 0,
    "digitalEmergencySystemId": 0,
    "aprsReportMode": "Off",
    "unknown1C_1_0": 0,
    "voxFunction": False,
    "scramble": False,
    "compander": False,
    "talkback": False,
    "unknown1D_3_0": 0,
    "squelchLevel": 3,
    "pttIdDisplay": False,
    "pttId": 0,
    "colorCode": 0,
    "companderDup": False,
    "voxRelated": False,
    "unknown25_7_6": 0,
    "unknown25_3_0": 0,
    "pttIdDisplay2": False,
    "rxSquelchMode": "Carrier/CTC",
    "unknown26_3_1": 0,
    "unknown26_0": False,
    "stepFrequency": 5,
    "signalingType": "None",
    "pttIdType": "Off",
    "unknown29_3_2": 0,
    "unknown29_1_0": 0,
    "unknown2A": 0,
    "contactId": 0,
}
