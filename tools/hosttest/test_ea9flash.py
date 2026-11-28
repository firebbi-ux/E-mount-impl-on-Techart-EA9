#!/usr/bin/env python3
"""Offline tests for the EA9 USB flasher's readback and write-boundary logic."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mac_updater")))
import ea9flash
import ea9_dump


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
    check(dev.addresses == [0x5000, 0x5040, 0x5080],
          "readback must cover every 64-byte chunk, including the tail")

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
    snapshot = ea9_dump.read_flash_range(fake_read, 0x1fff0, 80)
    check(len(snapshot) == 80, "full-flash reader must trim the final partial report")
    check(read_addresses == [0x1fff0, 0x20030], "full-flash reader must advance by 64 bytes")

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
