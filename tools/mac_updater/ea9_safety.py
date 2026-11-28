#!/usr/bin/env python3
"""Safety checks and bounded restore helpers for EA9 full-flash backups."""

FLASH_SIZE = 0x20000
BOOTLOADER_END = 0x5000
APP_BASE = 0x5000
APP_END = 0x16000
APP_SIZE = APP_END - APP_BASE
LOG_BASE = APP_END
REPORT_LEN = 64


def validate_backup(data):
    """Validate the complete backup and return its bootloader/app slices."""
    if len(data) != FLASH_SIZE:
        raise ValueError("backup must be exactly 128 KiB")
    boot = data[:BOOTLOADER_END]
    if not boot or all(b == 0xFF for b in boot) or all(b == 0x00 for b in boot):
        raise ValueError("bootloader backup region is blank or erased")
    if len(set(boot)) < 8:
        raise ValueError("bootloader backup region has implausibly little data")
    return boot, data[APP_BASE:APP_END]


def verify_bootloader_reads(backup_boot, first_read, second_read):
    """Require two independent live reads to match the saved bootloader."""
    expected = BOOTLOADER_END
    if len(backup_boot) != expected or len(first_read) != expected or len(second_read) != expected:
        raise ValueError("bootloader region read is incomplete")
    if first_read != second_read:
        raise ValueError("bootloader changed between independent reads")
    if first_read != backup_boot:
        raise ValueError("connected EA9 bootloader does not match this backup")
    if all(b == 0xFF for b in backup_boot) or all(b == 0x00 for b in backup_boot):
        raise ValueError("bootloader backup region is blank or erased")
    return True


def restore_application_payload(data):
    """Return the exact app partition; never include bootloader or TRACE LOG."""
    _boot, app = validate_backup(data)
    if len(app) != APP_SIZE:
        raise ValueError("backup application region is incomplete")
    return app
