#!/usr/bin/env python3
"""Decode TRACE snapshots from a full-flash or LOG-region USB dump."""

import argparse
import csv
import struct
import sys
import zlib

FLASH_SIZE = 0x20000
LOG_BASE = 0x16000
LOG_SIZE = 0x6000
SLOT_SIZE = 0x1800
HEADER_SIZE = 64
MAGIC = 0x45545243
VERSION = 1
CAPACITY = 256
RECORD = struct.Struct("<IiBBH")
HEADER = struct.Struct("<16I")
KINDS = {
    1: "BEGIN",
    2: "TARGET",
    3: "SAMPLE",
    4: "EVENT_QUEUED",
    5: "EVENT_SENT",
    6: "CANCELLED",
    7: "FINISHED",
}


def _slot_region(blob):
    if len(blob) == FLASH_SIZE:
        return blob[LOG_BASE:LOG_BASE + LOG_SIZE], 0
    if len(blob) == LOG_SIZE:
        return blob, 0
    if len(blob) == SLOT_SIZE:
        return blob, 0
    raise ValueError(
        "input must be a 128 KiB full-flash dump, 24 KiB LOG dump, "
        "or one 6 KiB TRACE slot"
    )


def decode_slot(raw, slot_index):
    if len(raw) != SLOT_SIZE:
        raise ValueError("TRACE slot must be exactly 0x1800 bytes")
    if raw == b"\\xff" * SLOT_SIZE:
        return None

    words = HEADER.unpack_from(raw, 0)
    (magic, version, sequence, count, write_count, retained_count,
     overwritten_count, dropped, session, session_first, start_us,
     data_crc, header_crc) = words[:13]

    if magic != MAGIC or version != VERSION:
        raise ValueError("slot %d: invalid magic/version" % slot_index)
    if count == 0 or count > CAPACITY or retained_count != count:
        raise ValueError("slot %d: invalid record count" % slot_index)
    if (zlib.crc32(raw[:48]) & 0xFFFFFFFF) != header_crc:
        raise ValueError("slot %d: header CRC mismatch" % slot_index)

    length = count * RECORD.size
    if HEADER_SIZE + length > SLOT_SIZE:
        raise ValueError("slot %d: records exceed slot bounds" % slot_index)
    record_bytes = raw[HEADER_SIZE:HEADER_SIZE + length]
    if (zlib.crc32(record_bytes) & 0xFFFFFFFF) != data_crc:
        raise ValueError("slot %d: record CRC mismatch" % slot_index)

    records = []
    first_t = None
    for index in range(count):
        t_us, pos, kind, detail, reserved = RECORD.unpack_from(
            record_bytes, index * RECORD.size
        )
        if first_t is None:
            first_t = t_us
        records.append({
            "record_index": index,
            "t_us": t_us,
            "elapsed_ms": ((t_us - first_t) & 0xFFFFFFFF) / 1000.0,
            "pos": pos,
            "kind": KINDS.get(kind, "UNKNOWN_%d" % kind),
            "kind_id": kind,
            "detail": detail,
            "reserved": reserved,
        })

    return {
        "slot": slot_index,
        "sequence": sequence,
        "write_count": write_count,
        "retained_count": retained_count,
        "overwritten_count": overwritten_count,
        "dropped": dropped,
        "session": session,
        "session_first": session_first,
        "start_us": start_us,
        "records": records,
    }


def decode_dump(blob):
    region, _ = _slot_region(blob)
    slot_count = len(region) // SLOT_SIZE
    decoded = []
    for index in range(slot_count):
        raw = region[index * SLOT_SIZE:(index + 1) * SLOT_SIZE]
        try:
            item = decode_slot(raw, index)
        except ValueError as exc:
            print("WARNING: %s" % exc, file=sys.stderr)
            continue
        if item is not None:
            decoded.append(item)
    return decoded


def write_csv(slots, stream):
    writer = csv.writer(stream)
    writer.writerow([
        "slot", "sequence", "record_index", "t_us", "elapsed_ms",
        "position_counts", "kind", "kind_id", "detail", "reserved",
    ])
    for slot in slots:
        for record in slot["records"]:
            writer.writerow([
                slot["slot"], slot["sequence"], record["record_index"],
                record["t_us"], "%.3f" % record["elapsed_ms"], record["pos"],
                record["kind"], record["kind_id"], record["detail"],
                record["reserved"],
            ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", help="full-flash or LOG-region dump file")
    parser.add_argument("-o", "--out", help="CSV output path (default: stdout)")
    parser.add_argument("--slot", type=int, choices=range(4),
                        help="decode only this slot")
    args = parser.parse_args()

    with open(args.dump, "rb") as source:
        slots = decode_dump(source.read())
    if args.slot is not None:
        slots = [slot for slot in slots if slot["slot"] == args.slot]

    for slot in slots:
        print(
            "slot {slot}: sequence={sequence}, records={retained_count}, "
            "overwritten={overwritten_count}, dropped={dropped}".format(**slot),
            file=sys.stderr,
        )
    if not slots:
        print("No valid TRACE snapshots found.", file=sys.stderr)

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as target:
            write_csv(slots, target)
    else:
        write_csv(slots, sys.stdout)


if __name__ == "__main__":
    main()
