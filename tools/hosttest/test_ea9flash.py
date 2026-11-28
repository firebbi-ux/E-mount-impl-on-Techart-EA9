#!/usr/bin/env python3
"""Offline tests for the EA9 USB flasher's readback and write-boundary logic."""
import os
import sys
import struct
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mac_updater")))
import ea9flash
import ea9_dump
import ea9_trace_decode


class FakeReadback:
    def __init__(self, image, base=ea9flash.APP_BASE):
        self.image = image
        self.base = base
        self.addresses = []

    def read_at(self, address):
        self.addresses.append(address)
        offset = address - self.base
        return self.image[offset:offset + ea9flash.CHUNK].ljust(ea9flash.CHUNK, b"\x00")


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def main():
    image = bytes((i * 37) & 0xff for i in range(130))
    dev = FakeReadback(image)
    ea9flash.verify_image(dev, image, progress=False)
    check(dev.addresses == list(range(0x5000, 0x5000 + ea9flash.transfer_length(len(image)), 64)),
          "readback must cover the full padded transfer, including the tail")

    damaged = bytearray(image)
    damaged[70] ^= 0x01
    try:
        ea9flash.verify_image(FakeReadback(bytes(damaged)), image, progress=False)
    except IOError as exc:
        check("0x00005046" in str(exc), "mismatch must report exact flash address")
    else:
        raise AssertionError("readback mismatch was not detected")

    read_addresses = []
    def fake_read(address):
        read_addresses.append(address)
        return bytes((address + i) & 0xff for i in range(64))
    snapshot = ea9_dump.read_flash_range(fake_read, 0x1ff80, 100)
    check(len(snapshot) == 100, "full-flash reader must trim the final partial report")
    check(read_addresses == [0x1ff80, 0x1ffc0], "full-flash reader must advance by 64 bytes")

    slot = bytearray(b"\xff" * 0x400)
    records = struct.pack("<IiBBH", 1000, 123, 1, 0, 0) + struct.pack("<IiBBH", 1000, 456, 2, 0, 0)
    header = [0xffffffff] * 16
    header[0:12] = [0x45545243, 2, 7, 2, 2, 2, 0, 0, 1, 0, 1000, zlib.crc32(records) & 0xffffffff]
    header[12] = zlib.crc32(struct.pack("<12I", *header[:12])) & 0xffffffff
    slot[:64] = struct.pack("<16I", *header)
    slot[64:64 + len(records)] = records
    decoded = ea9_trace_decode.decode_dump(bytes(slot))
    check(len(decoded) == 1 and decoded[0]["sequence"] == 7,
          "TRACE decoder must accept a valid snapshot")
    check(decoded[0]["records"][1]["pos"] == 456,
          "TRACE decoder must preserve signed encoder positions")
    damaged_slot = bytearray(slot)
    damaged_slot[70] ^= 1
    check(ea9_trace_decode.decode_dump(bytes(damaged_slot)) == [],
          "TRACE decoder must reject corrupt record data")

    check(ea9flash.transfer_length(29276) == 29760,
          "transfer length must include the final ACK-clocked chunk")

    class NoTouch:
        checked = False

        def check(self):
            self.checked = True
            return (1, 8, 0)

    no_touch = NoTouch()
    try:
        ea9flash.flash(no_touch, b"X" * 69632, progress=False)
    except ValueError:
        pass
    else:
        raise AssertionError("image crossing LOG boundary was not rejected")
    check(not no_touch.checked, "oversize image must be rejected before USB CHECK/START")

    print("PASS: EA9 USB readback, mismatch address, transfer sizing, flash boundary")


if __name__ == "__main__":
    main()
