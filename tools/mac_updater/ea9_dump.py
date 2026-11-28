#!/usr/bin/env python3
"""Read-only full-flash backup for the Techart LM-EA9 USB HID bootloader."""

import argparse
import hashlib
import struct
import sys

MAGIC = bytes((0x95, 0x27, 0x68, 0x18))
CMD_READ = bytes((0x03, 0x03))
REPORT_LEN = 64
FLASH_SIZE = 0x20000
DEFAULT_VID = 0x0483
DEFAULT_PID = 0x575A


def parse_range(value):
    try:
        start_s, length_s = value.split(":", 1)
        start, length = int(start_s, 0), int(length_s, 0)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("range must be START:LENGTH, e.g. 0x0:0x20000")
    if start < 0 or length <= 0 or start + length > FLASH_SIZE:
        raise argparse.ArgumentTypeError("range must stay inside 0x00000..0x20000")
    return start, length


def read_flash_range(read_at, start, length, progress=None):
    """Read an arbitrary byte range using the bootloader's 64-byte raw READ."""
    if start < 0 or length < 0 or start + length > FLASH_SIZE:
        raise ValueError("read range is outside the 128 KiB flash")
    out = bytearray()
    while len(out) < length:
        chunk = read_at(start + len(out))
        if len(chunk) != REPORT_LEN:
            raise IOError("READ did not return exactly 64 raw bytes")
        out.extend(chunk[:min(REPORT_LEN, length - len(out))])
        if progress is not None:
            progress(len(out), length)
    return bytes(out)


class BootloaderReader:
    def __init__(self, device):
        self.device = device

    def read_at(self, address):
        payload = MAGIC + CMD_READ + struct.pack("<I", address)
        report = b"\x00" + payload + b"\x00" * (REPORT_LEN - len(payload))
        self.device.write(report)
        reply = self.device.read(REPORT_LEN, 5000)
        if not reply:
            raise TimeoutError("No READ response at 0x%08x" % address)
        return bytes(reply)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", type=parse_range, default=(0, FLASH_SIZE),
                    help="START:LENGTH (default: 0x0:0x20000, the complete flash)")
    ap.add_argument("-o", "--out", required=True, help="output backup file")
    ap.add_argument("--vid", type=lambda x: int(x, 16), default=DEFAULT_VID)
    ap.add_argument("--pid", type=lambda x: int(x, 16), default=DEFAULT_PID)
    args = ap.parse_args()

    try:
        import hid
    except ImportError:
        ap.error("hidapi is required: python3 -m pip install hidapi")

    devices = hid.enumerate(args.vid, args.pid)
    if not devices:
        sys.exit("No EA9 bootloader found (USB HID %04x:%04x)." % (args.vid, args.pid))
    if len(devices) != 1:
        sys.exit("Found %d matching EA9 devices; connect only the adapter to back up." % len(devices))

    dev = hid.device()
    try:
        dev.open_path(devices[0]["path"])
        dev.set_nonblocking(0)
        reader = BootloaderReader(dev)
        start, length = args.range
        print("READ-only backup: 0x%05x..0x%05x (%d bytes)" %
              (start, start + length, length))
        def show_progress(done, total):
            if done % 4096 == 0 or done == total:
                print("  read %d/%d bytes" % (done, total))

        data = read_flash_range(reader.read_at, start, length, show_progress)
        with open(args.out, "wb") as f:
            f.write(data)
        print("Saved %d bytes to %s" % (len(data), args.out))
        print("SHA-256: %s" % hashlib.sha256(data).hexdigest())
        print("No START or DATA command was sent; flash contents were not modified.")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
