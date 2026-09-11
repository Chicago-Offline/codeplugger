// Verification harness for codeplugger's NeonPlug exporter (issue #4).
//
// This file is NOT run by pytest directly. `tests/test_neonplug.py`'s
// `test_neonplug_import_and_dm32uv_codec_roundtrip` copies it into a local
// NeonPlug checkout's `tests/unit/` directory and runs it with that
// checkout's own `vitest`, so it exercises NeonPlug's REAL import path and
// REAL DM-32UV byte codec -- not a reimplementation of either.
//
// To reproduce manually:
//   git clone https://github.com/infamy/NeonPlug.git /tmp/NeonPlug
//   cd /tmp/NeonPlug && git checkout 8ae184770e03a93959f81c262f2ba9dcb93b0400
//   npm install
//   cp <this file> tests/unit/codeplugger_fixture.test.ts
//   CODEPLUGGER_FIXTURE=/path/to/fixture.neonplug npx vitest run tests/unit/codeplugger_fixture.test.ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { importCodeplug } from "../../src/services/codeplugExport";
import { validateChannelForEncoding } from "../../src/utils/channelHelpers";
import {
  encodeChannel,
  parseChannel,
  encodeZone,
  parseZones,
} from "../../src/radios/dm32uv/structures";

const FIXTURE_PATH = process.env.CODEPLUGGER_FIXTURE;

describe("codeplugger NeonPlug exporter fixture", () => {
  it("imports base-free and survives the DM-32UV codec round trip", async () => {
    if (!FIXTURE_PATH) {
      throw new Error("CODEPLUGGER_FIXTURE env var must point at a generated .neonplug file");
    }
    const bytes = readFileSync(FIXTURE_PATH);
    const file = new File([bytes], "fixture.neonplug", { type: "application/zip" });

    // Base-free: import a file this exporter wrote with no prior/base
    // NeonPlug export involved, and no radioSettings/radioInfo/etc. present.
    const codeplug = await importCodeplug(file);
    expect(codeplug.channels).toHaveLength(3);
    expect(codeplug.zones).toHaveLength(1);
    expect(codeplug.channels.map((c) => c.name)).toEqual(["SIMPLEX", "REPEATER", "RXONLY"]);
    expect(codeplug.zones[0].channels).toEqual([1, 2, 3]);

    for (const ch of codeplug.channels) {
      // NeonPlug's own required-fields check for a safe write.
      expect(() => validateChannelForEncoding(ch)).not.toThrow();
      expect(validateChannelForEncoding(ch)).toBe(true);

      // Round-trip through the real DM-32UV byte codec.
      const encoded = encodeChannel(ch);
      const decoded = parseChannel(encoded, ch.number);

      expect(decoded.rxFrequency).toBeCloseTo(ch.rxFrequency, 6);
      expect(decoded.txFrequency).toBeCloseTo(ch.txFrequency, 6);
      expect(decoded.forbidTx).toBe(ch.forbidTx);
      expect(decoded.rxCtcssDcs).toEqual(ch.rxCtcssDcs);
      expect(decoded.txCtcssDcs).toEqual(ch.txCtcssDcs);
      expect(decoded.bandwidth).toBe(ch.bandwidth);
      expect(decoded.power).toBe(ch.power);
    }

    const zone = codeplug.zones[0];
    const block = new Uint8Array(4096).fill(0xff);
    block.set(encodeZone(zone, 0), 16);
    const parsedZones = parseZones(block);
    expect(parsedZones[0]?.name).toBe(zone.name);
    expect(parsedZones[0]?.channels).toEqual(zone.channels);
  });
});
