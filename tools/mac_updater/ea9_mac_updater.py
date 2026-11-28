#!/usr/bin/env python3
"""macOS GUI flasher for the project-built EA9 TRACE test firmware."""

import os
import re
import sys
import struct
import hashlib
import logging
import faulthandler
import hid
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QTextEdit, QVBoxLayout, QWidget,
)

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
    EA9_BIN = os.path.join(BASE_DIR, "firmware", "ea9-rebuild-trace.bin")
    FW_CS = os.path.join(BASE_DIR, "win_src", "Firmware.cs")
    WIN_DOCS_DIR = os.path.join(BASE_DIR, "win_docs")
    HW_IMG = os.path.join(BASE_DIR, "win_assets", "hw_version.jpg")
else:
    REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(REPO_DIR, "tools"))
    sys.path.insert(0, os.path.join(REPO_DIR, "tools", "win_updater"))
    EA9_BIN = os.path.join(REPO_DIR, "build", "ea9-rebuild-trace.bin")
    FW_CS = os.path.join(REPO_DIR, "tools", "win_updater", "src", "Firmware.cs")
    WIN_DOCS_DIR = os.path.join(REPO_DIR, "tools", "win_updater")
    HW_IMG = os.path.join(REPO_DIR, "tools", "win_updater", "assets", "hw_version.jpg")

import cfgblock     # noqa: E402
import ea9_safety   # noqa: E402
import gen_optical  # noqa: E402
import lens_config_txt  # noqa: E402

def _configure_diagnostics():
    try:
        log_dir = os.path.join(os.path.expanduser("~"), "Library", "Logs", "EA9ProjectTRACE")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "ea9-trace-lab.log")
        logging.basicConfig(filename=log_path, level=logging.INFO,
            format="%(asctime)s %(levelname)s %(threadName)s %(message)s", encoding="utf-8")
        handle = open(log_path, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=handle, all_threads=True)
        return handle
    except Exception:
        return None

_DIAGNOSTIC_FILE = _configure_diagnostics()

def _handle_uncaught_exception(exc_type, exc_value, exc_tb):
    logging.critical("Uncaught application exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _handle_uncaught_exception

VID = 0x0483
PID = 0x575A
MAGIC = b"\x95\x27\x68\x18"
CMD_START = b"\x01\x01"
CMD_CHECK = b"\x02\x02"
CMD_READ = b"\x03\x03"
REPORT_LEN = 64
APP_BASE = 0x00005000
APP_SAFE_END = 0x00016000
SLOT_APERTURES = ("f/2.5", "f/2.8", "f/3.2", "f/3.5", "f/4.0", "f/4.5", "f/5.0", "f/5.6")
DEFAULT_NAME = "Community EA9 Adapter"
FOCAL_MIN, FOCAL_MAX = 8, 600
XPD_MIN, XPD_MAX = 1.0, 1000.0


def load_declared_version():
    try:
        with open(FW_CS, "r", encoding="utf-8-sig") as f:
            txt = f.read()
        m = re.search(r"DeclaredVersion\s*=\s*\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}", txt)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except OSError:
        pass
    return (2, 4, 0)


def load_changelog(zh):
    fname = "changelog.zh.txt" if zh else "changelog.txt"
    path = os.path.join(WIN_DOCS_DIR, fname)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError as e:
        return f"(无法读取更新说明 / Release notes unavailable: {e})"


class HelpDialog(QDialog):
    def __init__(self, parent, title, ok_text, sections, image_path=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        if image_path:
            pix = QPixmap(image_path)
            if not pix.isNull():
                if pix.width() > 540:
                    pix = pix.scaledToWidth(540, Qt.SmoothTransformation)
                img_lbl = QLabel()
                img_lbl.setPixmap(pix)
                img_lbl.setAlignment(Qt.AlignCenter)
                v.addWidget(img_lbl)
            else:
                v.addWidget(QLabel(f"(picture unavailable: {image_path})"))

        for heading, body in sections:
            if heading:
                hl = QLabel(f"<b>{heading}</b>")
                v.addWidget(hl)
            bl = QLabel(body)
            bl.setWordWrap(True)
            bl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(bl)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.button(QDialogButtonBox.Ok).setText(ok_text)
        btns.accepted.connect(self.accept)
        v.addWidget(btns)


class HidBootloader:
    def __init__(self, path):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(0)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def send(self, payload):
        buf = b"\x00" + payload + b"\x00" * (REPORT_LEN - len(payload))
        self.dev.write(buf)

    def recv(self, timeout_ms=5000):
        rep = self.dev.read(REPORT_LEN, timeout_ms)
        return bytes(rep) if rep else None

    def _expect(self, cmd):
        rep = self.recv()
        if rep is None:
            raise TimeoutError("Device timed out / 设备响应超时")
        if rep[:4] != MAGIC or rep[4:6] != cmd:
            raise IOError(f"Bad reply / 异常响应: {rep[:8].hex(' ')}")
        return rep

    def check(self):
        self.send(MAGIC + CMD_CHECK)
        rep = self._expect(CMD_CHECK)
        return (rep[6], rep[7], rep[8])

    def start(self, version):
        self.send(MAGIC + CMD_START + bytes(version))
        self._expect(CMD_START)

    def read_at(self, address):
        self.send(MAGIC + CMD_READ + struct.pack("<I", address))
        rep = self.recv()
        if rep is None:
            raise TimeoutError(f"No readback from flash 0x{address:08x}")
        return rep


class BackupWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(bool, str)

    def __init__(self, path, output_path, zh):
        super().__init__()
        self.path = path
        self.output_path = output_path
        self.zh = zh

    def run(self):
        try:
            total = 0x20000
            data = bytearray()
            with HidBootloader(self.path) as dev:
                version = dev.check()
                self.progress.emit(0, (
                    f"已连接引导程序 {version[0]}.{version[1]}.{version[2]}，开始只读备份…"
                    if self.zh else
                    f"Bootloader {version[0]}.{version[1]}.{version[2]} connected; starting read-only backup..."
                ))
                for address in range(0, total, REPORT_LEN):
                    chunk = dev.read_at(address)
                    if chunk is None or len(chunk) != REPORT_LEN:
                        raise IOError(f"Invalid READ response at 0x{address:05x}")
                    data.extend(chunk)
                    pct = min(100, len(data) * 100 // total)
                    if pct % 5 == 0:
                        self.progress.emit(pct, (
                            f"只读备份中… {pct}%"
                            if self.zh else f"Read-only backup... {pct}%"
                        ))
                # One sequential full-flash read is the backup. Do not issue
                # a second USB pass over the bootloader after reaching 100%.
                # File integrity is checked locally after the HID handle closes.
            if len(data) != total:
                raise IOError(f"Backup length is {len(data)}, expected {total}")
            ea9_safety.validate_backup(bytes(data))
            digest = hashlib.sha256(data).hexdigest()
            temp_path = self.output_path + ".partial"
            try:
                with open(temp_path, "wb") as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                with open(temp_path, "rb") as check_file:
                    saved = check_file.read()
                if len(saved) != total or hashlib.sha256(saved).hexdigest() != digest:
                    raise IOError("saved backup failed size/SHA-256 verification")
                os.replace(temp_path, self.output_path)
                manifest_path = self.output_path + ".sha256"
                manifest_temp = manifest_path + ".partial"
                with open(manifest_temp, "w", encoding="ascii") as manifest:
                    manifest.write(ea9_safety.make_sha256_manifest(saved))
                    manifest.flush()
                    os.fsync(manifest.fileno())
                with open(manifest_temp, "r", encoding="ascii") as manifest:
                    ea9_safety.verify_sha256_manifest(saved, manifest.read())
                os.replace(manifest_temp, manifest_path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                if os.path.exists(self.output_path + ".sha256.partial"):
                    os.unlink(self.output_path + ".sha256.partial")
            sidecar_note = ""
            try:
                lens_slots = lens_config_txt.slots_from_flash(bytes(data))
                txt_path = os.path.splitext(self.output_path)[0] + "-lens-config.txt"
                txt_temp = txt_path + ".partial"
                with open(txt_temp, "w", encoding="utf-8") as txt_file:
                    txt_file.write(lens_config_txt.format_txt(lens_slots))
                    txt_file.flush()
                    os.fsync(txt_file.fileno())
                os.replace(txt_temp, txt_path)
                sidecar_note = f"\n镜头清单：{txt_path}" if self.zh else f"\nLens list: {txt_path}"
            except Exception as sidecar_error:
                sidecar_note = (f"\n警告：备份成功，但镜头清单生成失败：{sidecar_error}"
                                if self.zh else
                                f"\nWarning: backup succeeded, but lens TXT generation failed: {sidecar_error}")
                logging.exception("Lens TXT sidecar generation failed")
            self.completed.emit(True, (
                f"完整 Flash 备份完成（128 KiB）。\nSHA-256：{digest}{sidecar_note}\n未发送任何写入指令。现在可以安全拔下 USB 线。"
                if self.zh else
                f"Full flash backup complete (128 KiB).\nSHA-256: {digest}{sidecar_note}\nNo write command was sent. You may safely unplug the USB cable."
            ))
        except Exception as e:
            self.completed.emit(False, (
                f"备份失败：{e}" if self.zh else f"Backup failed: {e}"
            ))


class FlashWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, path, image, declare_ver, zh):
        super().__init__()
        self.path = path
        self.image = image
        self.declare_ver = declare_ver
        self.zh = zh

    def run(self):
        try:
            limit = (len(self.image) // 1024) * 1024 + 1024
            transfer_bytes = limit + REPORT_LEN
            if APP_BASE + transfer_bytes > APP_SAFE_END:
                raise ValueError(
                    f"Image plus bootloader padding ends at 0x{APP_BASE + transfer_bytes:08x}, "
                    f"past LOG boundary 0x{APP_SAFE_END:08x}"
                )
            with HidBootloader(self.path) as dev:
                v = dev.check()
                msg0 = (f"已找到转接环（引导程序记录 {v[0]}.{v[1]}.{v[2]}），正在准备…"
                        if self.zh else
                        f"Found adapter (bootloader {v[0]}.{v[1]}.{v[2]}). Preparing...")
                self.progress.emit(0, msg0)
                dev.start(self.declare_ver)
                pos = 0
                last_pct = -1
                while pos <= limit:
                    dev.send(self.image[pos:pos + REPORT_LEN])
                    pos += REPORT_LEN
                    if dev.recv() is None:
                        raise TimeoutError(f"Stopped acking at offset {pos}")
                    pct = min(100, pos * 100 // limit)
                    if pct != last_pct:
                        last_pct = pct
                        wmsg = f"正在写入固件… {pct}%" if self.zh else f"Writing firmware... {pct}%"
                        self.progress.emit(pct, wmsg)
                verify_msg = ("正在回读校验固件…" if self.zh else "Reading firmware back for verification...")
                self.progress.emit(100, verify_msg)
                for offset in range(0, transfer_bytes, REPORT_LEN):
                    actual = dev.read_at(APP_BASE + offset)
                    expected = self.image[offset:offset + REPORT_LEN].ljust(REPORT_LEN, b"\x00")
                    if actual != expected:
                        mismatch = next((i for i, (a, e) in enumerate(zip(actual, expected)) if a != e), 0)
                        raise IOError(
                            f"Readback mismatch at flash 0x{APP_BASE + offset + mismatch:08x}"
                        )
            ok_msg = ("固件写入成功！现在请拔下 USB 线。"
                      if self.zh else
                      "The firmware was written successfully. Unplug the USB cable now.")
            self.finished.emit(True, ok_msg)
        except Exception as e:
            err_msg = f"写入失败：{e}" if self.zh else f"Writing failed: {e}"
            self.finished.emit(False, err_msg)


class RestoreWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, path, backup_path, declare_ver, zh):
        super().__init__()
        self.path = path
        self.backup_path = backup_path
        self.declare_ver = declare_ver
        self.zh = zh

    def _read_range(self, dev, start, end):
        data = bytearray()
        for address in range(start, end, REPORT_LEN):
            chunk = dev.read_at(address)
            if len(chunk) != REPORT_LEN:
                raise IOError(f"Incomplete flash READ at 0x{address:05x}")
            data.extend(chunk)
        return bytes(data)

    def run(self):
        try:
            with open(self.backup_path, "rb") as source:
                backup = source.read()
            backup_boot, _ = ea9_safety.validate_backup(backup)
            payload = ea9_safety.restore_application_payload(backup)
            digest = hashlib.sha256(backup).hexdigest()
            manifest_path = self.backup_path + ".sha256"
            with open(manifest_path, "r", encoding="ascii") as manifest:
                ea9_safety.verify_sha256_manifest(backup, manifest.read())
            with HidBootloader(self.path) as dev:
                current_version = dev.check()
                first = self._read_range(dev, 0, ea9_safety.BOOTLOADER_END)
                second = self._read_range(dev, 0, ea9_safety.BOOTLOADER_END)
                ea9_safety.verify_bootloader_reads(backup_boot, first, second)
                log_before = self._read_range(dev, ea9_safety.LOG_BASE, ea9_safety.FLASH_SIZE)
                self.progress.emit(0, (
                    f"引导区与备份匹配（SHA-256 {digest[:16]}…），开始恢复原应用固件…"
                    if self.zh else
                    f"Bootloader matches backup (SHA-256 {digest[:16]}…); restoring original application..."
                ))
                # Preserve the bootloader's current version declaration; this
                # is bookkeeping separate from the camera-facing version in the app.
                dev.start(current_version)
                self.progress.emit(0, (
                    "正在恢复并逐字节校验原应用区…"
                    if self.zh else "Restoring and verifying original application byte-for-byte..."
                ))
                ea9_safety.restore_application_region(
                    dev, payload,
                    lambda pct: self.progress.emit(pct, (
                        f"正在恢复原应用区… {pct}%"
                        if self.zh else f"Restoring original application... {pct}%"
                    ))
                )
                boot_after = self._read_range(dev, 0, ea9_safety.BOOTLOADER_END)
                log_after = self._read_range(dev, ea9_safety.LOG_BASE, ea9_safety.FLASH_SIZE)
                if boot_after != backup_boot:
                    raise IOError("USB bootloader region changed during restore")
                if log_after != log_before:
                    raise IOError("TRACE LOG region changed during restore")
                if dev.check() != current_version:
                    raise IOError("bootloader version declaration changed during restore")
            self.finished.emit(True, (
                "原应用区已恢复并通过逐字节回读校验。USB 引导区与 TRACE 日志区未写入。现在可以安全拔下 USB 线。"
                if self.zh else
                "Original application restored and verified byte-for-byte. USB bootloader and TRACE LOG were not written. You may safely unplug the USB cable."
            ))
        except Exception as e:
            self.finished.emit(False, (
                f"恢复失败：{e}" if self.zh else f"Restore failed: {e}"
            ))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.declare_ver = (2, 4, 1)  # bootloader declaration; camera 0x07 version remains in image
        self.trace_sha256 = None
        self.zh = True
        self.base_image = None
        self.seen_path = None
        self.device_ok = False
        self.busy = False
        self.worker = None
        self.backup_worker = None
        self.backup_output_path = None
        self.config_loaded_from_flash = False
        self.boot_ver = None

        self.resize(900, 800)
        self.setMinimumSize(820, 720)
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setSpacing(8)

        # 顶部：更新说明标签 + 语言切换
        top_row = QHBoxLayout()
        self.notes_lbl = QLabel()
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["中文", "English"])
        self.lang_combo.currentIndexChanged.connect(self.on_lang_changed)
        top_row.addWidget(self.notes_lbl)
        top_row.addStretch()
        top_row.addWidget(self.lang_combo)
        lay.addLayout(top_row)

        # 更新说明文本框
        self.notes_box = QTextEdit()
        self.notes_box.setReadOnly(True)
        self.notes_box.setMinimumHeight(140)
        lay.addWidget(self.notes_box, 1)

        # 硬件版本 + 帮助链接
        hw_row = QHBoxLayout()
        self.hw_lbl = QLabel()
        self.hw_combo = QComboBox()
        self.hw_combo.addItems(["2", "1"])
        self.hw_help = self._make_help_link(self.show_hw_help)
        hw_row.addWidget(self.hw_lbl)
        hw_row.addWidget(self.hw_combo)
        hw_row.addSpacing(8)
        hw_row.addWidget(self.hw_help)
        hw_row.addStretch()
        lay.addLayout(hw_row)

        # 模式选择 + 帮助链接
        mode_row = QHBoxLayout()
        self.mode_lbl = QLabel()
        self.multi_radio = QRadioButton()
        self.single_radio = QRadioButton()
        self.multi_radio.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.multi_radio)
        bg.addButton(self.single_radio)
        self.multi_radio.toggled.connect(self.update_mode)
        self.focal_help = self._make_help_link(self.show_focal_help)
        mode_row.addWidget(self.mode_lbl)
        mode_row.addWidget(self.multi_radio)
        mode_row.addWidget(self.single_radio)
        mode_row.addSpacing(8)
        mode_row.addWidget(self.focal_help)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # 单镜头面板
        self.single_box = QGroupBox()
        sg = QGridLayout(self.single_box)
        self.s_focal_lbl = QLabel()
        self.s_xpd_lbl = QLabel()
        self.s_name_lbl = QLabel()
        self.single_focal = QLineEdit("50")
        self.single_focal.setMaxLength(4)
        self.single_xpd = self._make_xpd_spin(45.0)
        self.single_xpd_help = self._make_help_link(self.show_xpd_help)
        self.single_name = QLineEdit(DEFAULT_NAME)
        self.single_name.setMaxLength(63)
        self.single_focal.textChanged.connect(lambda txt: self._auto_xpd(txt, self.single_xpd))

        xpd_row = QHBoxLayout()
        xpd_row.setContentsMargins(0, 0, 0, 0)
        xpd_row.addWidget(self.single_xpd)
        xpd_row.addSpacing(8)
        xpd_row.addWidget(self.single_xpd_help)
        xpd_row.addStretch()

        sg.addWidget(self.s_focal_lbl, 0, 0)
        sg.addWidget(self.single_focal, 0, 1)
        sg.addWidget(self.s_xpd_lbl, 1, 0)
        sg.addLayout(xpd_row, 1, 1)
        sg.addWidget(self.s_name_lbl, 2, 0)
        sg.addWidget(self.single_name, 2, 1)
        lay.addWidget(self.single_box)

        # 多镜头表格
        self.multi_box = QGroupBox()
        mg = QGridLayout(self.multi_box)
        self.col_heads = [QLabel() for _ in range(4)]
        for c, h in enumerate(self.col_heads):
            mg.addWidget(h, 0, c)

        self.slot_focal = []
        self.slot_name = []
        self.slot_xpd = []
        for i, ap in enumerate(SLOT_APERTURES):
            f = QLineEdit()
            f.setMaxLength(4)
            f.setMaximumWidth(90)
            n = QLineEdit()
            n.setMaxLength(63)
            n.setVisible(False)
            x = self._make_xpd_spin(45.0)
            x.setVisible(False)
            f.textChanged.connect(lambda _t, fn=f, nm=n, xp=x: self._on_slot_focal(fn, nm, xp))
            self.slot_focal.append(f)
            self.slot_name.append(n)
            self.slot_xpd.append(x)
            mg.addWidget(QLabel(ap), i + 1, 0)
            mg.addWidget(f, i + 1, 1)
            mg.addWidget(n, i + 1, 2)
            mg.addWidget(x, i + 1, 3)
        mg.setColumnStretch(2, 1)
        lay.addWidget(self.multi_box)

        # 多镜头模式右下角：如何选择出瞳距离？
        slot_help_row = QHBoxLayout()
        slot_help_row.addStretch()
        self.slot_xpd_help = self._make_help_link(self.show_xpd_help)
        slot_help_row.addWidget(self.slot_xpd_help)
        lay.addLayout(slot_help_row)

        # 设备与进度条
        self.device_lbl = QLabel()
        lay.addWidget(self.device_lbl)

        # 操作区分组：左侧处理本地备份，右侧处理设备固件。
        # 进度条独立占满整行，避免被长按钮挤压。
        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        backup_group = QGroupBox()
        backup_actions = QVBoxLayout(backup_group)
        backup_actions.setSpacing(8)
        self.backup_btn = QPushButton()
        self.backup_btn.setEnabled(False)
        self.backup_btn.clicked.connect(self.on_backup)
        self.load_lens_btn = QPushButton()
        self.load_lens_btn.clicked.connect(self.on_load_lens_config)
        self.new_lens_btn = QPushButton()
        self.new_lens_btn.clicked.connect(self.on_new_lens_config)
        self.save_lens_btn = QPushButton()
        self.save_lens_btn.clicked.connect(self.on_save_lens_config)
        backup_actions.addWidget(self.backup_btn)
        backup_actions.addWidget(self.load_lens_btn)
        backup_actions.addWidget(self.new_lens_btn)
        backup_actions.addWidget(self.save_lens_btn)

        device_group = QGroupBox()
        device_actions = QVBoxLayout(device_group)
        device_actions.setSpacing(8)
        self.restore_btn = QPushButton()
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self.on_restore)
        self.write_btn = QPushButton()
        self.write_btn.setEnabled(False)
        self.write_btn.clicked.connect(self.on_write)
        device_actions.addWidget(self.restore_btn)
        device_actions.addWidget(self.write_btn)

        action_row.addWidget(backup_group, 1)
        action_row.addWidget(device_group, 1)
        lay.addLayout(action_row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMinimumHeight(22)
        lay.addWidget(self.bar)

        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        try:
            with open(EA9_BIN, "rb") as bf:
                self.base_image = bf.read()
            if not self.base_image or len(self.base_image) > 0x10000:
                raise ValueError("TRACE image is empty or exceeds the application flash region")
            # The original updater stores lens calibration in the CfgAnchor block
            # inside the application image. Validate it before enabling any flash.
            cfgblock.find(self.base_image)
            self.trace_sha256 = hashlib.sha256(self.base_image).hexdigest()
        except Exception as e:
            self.base_image = None
            self.status_lbl.setText(f"无法读取内置 TRACE 固件 ea9-rebuild-trace.bin：{e}")

        self.apply_text()
        self.update_mode()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_usb)
        self.poll_timer.start(2000)
        self.poll_usb()

    def _t(self, en, zh):
        return zh if self.zh else en

    @staticmethod
    def _make_help_link(handler):
        lbl = QLabel()
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lbl.setOpenExternalLinks(False)
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.linkActivated.connect(lambda _href: handler())
        return lbl

    def show_hw_help(self):
        dlg = HelpDialog(
            self,
            self._t("Adapter Version", "转接环版本"),
            self._t("OK", "确定"),
            [
                (self._t("What is the difference", "有什么区别"),
                 self._t(
                     "Version 1 has less room past infinity than version 2. To focus at infinity, "
                     "version 1 has to retract all the way to its end stop. On version 2 the firmware "
                     "does not need to, which is easier on the mechanism and gives it a longer life.",
                     "版本 1 在无穷远之外的余量比版本 2 小。为了对焦到无穷远，版本 1 必须一直退到机械限位；"
                     "版本 2 的固件则不需要这样做，对机构的磨损更小，寿命也更长。"
                 )),
                (self._t("How to choose", "如何选择"),
                 self._t(
                     "Only if your adapter is version 1 hardware (see the picture) "
                     "and it cannot focus at infinity, try version 1.",
                     "只有当你的转接环是版本 1 的硬件（见上图），并且无法对焦到无穷远时，才尝试选择版本 1。"
                 )),
            ],
            image_path=HW_IMG,
        )
        dlg.exec()

    def show_focal_help(self):
        dlg = HelpDialog(
            self,
            self._t("Focal Length", "焦距"),
            self._t("OK", "确定"),
            [
                (self._t("Single Lens", "单镜头"),
                 self._t(
                     "Choose this if you only use one lens. The aperture is then fixed while you "
                     "shoot, so it cannot be changed by mistake.",
                     "如果你只使用一支镜头，请选择这一项。拍摄时光圈固定不变，不会被误操作改动。"
                 )),
                (self._t("Multiple Lenses", "多镜头"),
                 self._t(
                     "Enter each lens's details in the row of the aperture you want to use for it. "
                     "To change lenses, set the camera to that aperture and press the shutter: the "
                     "adapter will restart and switch to that lens's details. Remember to set the "
                     "aperture back to its widest value before you shoot again.",
                     "把每支镜头的信息填在你想用来选择它的那个光圈所在的行。换镜头时，把相机设为对应的光圈"
                     "并按下快门：转接环会重启，并切换到这支镜头的信息。再次拍摄前，记得把光圈调回最大。"
                 )),
            ],
        )
        dlg.exec()

    def show_xpd_help(self):
        dlg = HelpDialog(
            self,
            self._t("Exit Pupil", "出瞳距离"),
            self._t("OK", "确定"),
            [
                ("",
                 self._t(
                     "Exit pupil distance is the axial distance from the camera sensor plane to the lens's exit pupil "
                     "(the virtual image of the aperture stop viewed from the rear). It governs the chief ray angle at "
                     "the edges of the sensor and is used to compensate off-axis PDAF bias.\n\n"
                     "1. Exit pupil distance should not be assumed equal to focal length; in fact, in most cases it is not. "
                     "The initial default preselected by this software is for reference only; please refer to the typical "
                     "patterns of different optical designs below for testing and step-by-step adjustment:\n"
                     "- Rangefinder lenses (e.g. M-mount): compact, typically shorter exit pupil (wide angles often 30-45 mm).\n"
                     "- SLR wide/standard lenses (e.g. MD/M42): retrofocus/double-Gauss designs push the exit pupil farther "
                     "from the sensor (often 55-80 mm, longer than focal length).\n"
                     "- Telephoto lenses (85 mm+): telephoto designs often have an exit pupil shorter than their focal length "
                     "(typically 60-95 mm).\n\n"
                     "2. Calibration tip (AF-C mode): Focus on a high-contrast target near the left/right edges of the "
                     "phase-detect area. If the centre is sharp while the edges show consistent front/back focus, adjust "
                     "the value in 1-2 mm steps (and fine-tune in 0.5 mm steps) to compensate for lens optics, adapter "
                     "vignetting and the sensor filter stack.",
                     "出瞳距离是指在无限远对焦状态下，镜头“出瞳（从镜头后方看进去的光圈虚像）”到相机传感器焦平面之间的距离。"
                     "它决定了斜射光线进入传感器边缘的角度，直接影响画面左右两侧相位对焦（PDAF）的准确性。\n\n"
                     "1. 不可一概而论等于焦距，事实上大多数时候出瞳距离并不等效于焦距，本软件给出的原始出瞳值仅供参考，"
                     "请参照以下不同光学结构镜头的典型规律进行测试以及步进式调整：\n"
                     "• 旁轴镜头（如原生 M 口）：结构紧凑无反光镜箱，广角/标头出瞳距离通常较短（如 35mm 旁轴常在 32～42 mm）。\n"
                     "• 单反广角与标头（如 MD/M42）：反望远或双高斯结构会把出瞳推远，实际出瞳往往明显大于焦距（常见 55～80 mm）。\n"
                     "• 中长焦摄远镜头（85mm 及以上）：出瞳距离反而经常小于其焦距（多在 60～95 mm 区间）。\n\n"
                     "2. 实拍校准建议：\n"
                     "无镜片机械转接环本身不改变无限远基准出瞳，但转接环内壁与 EA9 内部通道可能对大光圈边缘光束造成轻微裁切。"
                     "若 AF-C 模式下画面正中心准焦而左右边缘偏焦，建议以 1～2 mm 为步进向大或向小测试，锁定方向后再用 0.5 mm 步进收口微调。"
                 )),
            ],
        )
        dlg.exec()

    def on_lang_changed(self, idx):
        self.zh = (idx == 0)
        self.apply_text()

    def apply_text(self):
        v = self.declare_ver
        self.setWindowTitle(self._t(
            "EA9 Project TRACE Lab — Calibration & Flasher",
            "EA9 Project TRACE 实验室 — 参数校准与固件烧录"
        ))
        self.notes_lbl.setText(self._t("Release notes", "更新说明"))
        self.notes_box.setPlainText(self._t(
            "PROJECT TRACE TEST BUILD\\nOriginal lens calibration UI restored: adapter version, single/multiple lens profiles, focal length, lens name and exit pupil.\\nLens calibration is encoded into the CfgAnchor block in the application image. The separate TRACE log region is preserved.\\nBase image SHA-256: " + str(self.trace_sha256),
            "PROJECT TRACE 测试构建\\n已恢复原版镜头参数界面：转接环版本、单/多镜头配置、焦距、镜头名称和出瞳距离。\\n镜头参数会写入应用程序镜像内的 CfgAnchor 配置块；独立 TRACE 日志区保持不变。\\n基础固件 SHA-256：" + str(self.trace_sha256)
        ))
        self.hw_lbl.setText(self._t("TRACE image:", "TRACE 固件："))
        how = self._t("How to choose?", "如何选择？")
        self.hw_help.setText(f'<a href="#">{how}</a>')
        self.focal_help.setText(f'<a href="#">{how}</a>')
        self.single_xpd_help.setText(f'<a href="#">{how}</a>')
        how_xpd = self._t("How to choose the exit pupil?", "如何选择出瞳距离？")
        self.slot_xpd_help.setText(f'<a href="#">{how_xpd}</a>')
        self.mode_lbl.setText(self._t("Focal length:", "焦距模式："))
        self.multi_radio.setText(self._t("Multiple Lenses (switch by aperture)", "多镜头（用光圈切换）"))
        self.single_radio.setText(self._t("Single Lens (fixed aperture)", "单镜头（固定光圈）"))
        self.single_box.setTitle(self._t("Single Lens Settings", "单镜头设置"))
        self.s_focal_lbl.setText(self._t("Focal length (mm):", "焦距 (mm)："))
        self.s_xpd_lbl.setText(self._t("Exit pupil (mm, 0.5 step):", "出瞳距离 (mm，0.5 步进)："))
        self.s_name_lbl.setText(self._t("Lens name (ASCII only):", "镜头名称（仅英文/数字/符号）："))
        self.multi_box.setTitle(self._t("Multiple Lenses (leave focal empty if unused)", "多镜头设置（焦距留空表示未启用）"))
        heads = (
            [self._t("Aperture", "光圈"), self._t("Focal (mm)", "焦距 (mm)"),
             self._t("Lens Name (ASCII)", "镜头名称 (ASCII)"), self._t("Exit Pupil (mm)", "出瞳距离 (mm)")]
        )
        for h, txt in zip(self.col_heads, heads):
            h.setText(txt)
        self.backup_btn.setText(self._t("Read-only full backup", "完整 Flash 只读备份"))
        self.load_lens_btn.setText(self._t("Open lens TXT", "打开镜头 TXT"))
        self.new_lens_btn.setText(self._t("New lens TXT", "新建镜头 TXT"))
        self.save_lens_btn.setText(self._t("Save lens TXT", "保存镜头 TXT"))
        self.restore_btn.setText(self._t("Restore from full backup", "从完整备份恢复原固件"))
        self.write_btn.setText(self._t("Flash project TRACE firmware", "烧入项目 TRACE 测试固件"))
        self._refresh_status_text()

    def _refresh_status_text(self):
        v = self.declare_ver
        if self.base_image is None:
            return
        if self.device_ok and self.boot_ver:
            b = self.boot_ver
            self.device_lbl.setText(self._t(
                f"Adapter: Connected, bootloader {b[0]}.{b[1]}.{b[2]}",
                f"转接环：已连接，引导程序版本 {b[0]}.{b[1]}.{b[2]}"
            ))
            self.status_lbl.setText(self._t(
                f"Ready to write firmware {v[0]}.{v[1]}.{v[2]}.",
                f"可以写入固件 {v[0]}.{v[1]}.{v[2]}。"
            ))
        elif self.seen_path is None:
            self.device_lbl.setText(self._t("Adapter: Not connected", "转接环：未连接"))
            self.status_lbl.setText(self._t(
                "Please connect the adapter to this Mac by USB (removed from camera).",
                "请用 USB 线将转接环连接到 Mac（请勿装在相机上）。"
            ))

    @staticmethod
    def _make_xpd_spin(default_val):
        s = QDoubleSpinBox()
        s.setRange(XPD_MIN, XPD_MAX)
        s.setSingleStep(0.5)
        s.setDecimals(1)
        s.setValue(float(default_val))
        return s

    @staticmethod
    def _auto_xpd(text, spin):
        t = text.strip()
        if t.isdigit() and int(t) > 0:
            val = gen_optical.auto_xpd(int(t))
            spin.setValue(float(max(XPD_MIN, min(XPD_MAX, val))))

    def _on_slot_focal(self, f_edit, n_edit, x_spin):
        t = f_edit.text().strip()
        active = t.isdigit() and int(t) > 0
        n_edit.setVisible(active)
        x_spin.setVisible(active)
        if active:
            self._auto_xpd(t, x_spin)

    def update_mode(self):
        single = self.single_radio.isChecked()
        self.single_box.setVisible(single)
        self.multi_box.setVisible(not single)
        self.slot_xpd_help.setVisible(not single)

    def poll_usb(self):
        if self.busy or self.base_image is None:
            return
        devs = hid.enumerate(VID, PID)
        path = devs[0]["path"] if devs else None
        if path == self.seen_path:
            return
        self.seen_path = path
        self.device_ok = False
        self.boot_ver = None
        self.write_btn.setEnabled(False)
        self.backup_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        if path is None:
            self._refresh_status_text()
            return
        try:
            with HidBootloader(path) as dev:
                self.boot_ver = dev.check()
            self.device_ok = True
            self._refresh_status_text()
            self.write_btn.setEnabled(True)
            self.backup_btn.setEnabled(True)
            self.restore_btn.setEnabled(True)
        except Exception as e:
            self.device_lbl.setText(self._t("Adapter: Not responding", "转接环：无响应"))
            self.status_lbl.setText(self._t(f"No answer ({e})", f"转接环没有响应（{e}）"))

    def on_backup(self):
        if not self.device_ok or not self.seen_path or self.busy:
            return
        default_name = "ea9-full-flash-backup.bin"
        output_path, _ = QFileDialog.getSaveFileName(
            self, self._t("Save full flash backup", "保存完整 Flash 备份"),
            os.path.join(os.path.expanduser("~"), default_name),
            "Binary files (*.bin);;All files (*)",
        )
        if not output_path:
            return
        prompt = self._t(
            "This operation only reads the adapter's 128 KiB flash. It does not erase or write firmware. Continue?",
            "此操作只读取转接环的 128 KiB Flash，不擦除、不写入固件。确认开始备份？"
        )
        if QMessageBox.question(
            self, self.windowTitle(), prompt,
            QMessageBox.Ok | QMessageBox.Cancel
        ) != QMessageBox.Ok:
            return

        self.busy = True
        self.backup_btn.setEnabled(False)
        self.write_btn.setEnabled(False)
        self.bar.setValue(0)
        self.backup_output_path = output_path
        self.backup_worker = BackupWorker(self.seen_path, output_path, self.zh)
        self.backup_worker.progress.connect(self._on_backup_progress)
        self.backup_worker.completed.connect(self.on_backup_done)
        self.backup_worker.start()

    def _on_backup_progress(self, pct, msg):
        self.bar.setValue(max(0, min(100, int(pct))))
        self.status_lbl.setText(str(msg))

    def on_backup_done(self, ok, msg):
        logging.info("Backup worker completed ok=%s message=%r", ok, msg)
        self.busy = False
        if self.device_ok:
            self.backup_btn.setEnabled(True)
            self.restore_btn.setEnabled(True)
            self.write_btn.setEnabled(True)
        self.status_lbl.setText(msg)
        if ok:
            self.bar.setValue(100)
            # Backup completion is intentionally independent from lens-config
            # parsing and UI mutation. Loading is a separate user-triggered action.
            logging.info("Backup UI completion: showing success dialog")
            QMessageBox.information(self, self.windowTitle(), msg)
            logging.info("Backup UI completion: success dialog closed")
        else:
            logging.error("Backup worker reported failure: %s", msg)
            QMessageBox.critical(self, self.windowTitle(), msg)
        # Do not immediately reopen the HID handle from this completion slot.
        # The backup worker has closed it; normal polling handles later USB events.
        logging.info("Backup completion UI settled; device_ok=%s path_present=%s",
                     self.device_ok, bool(self.seen_path))

    def _apply_lens_slots(self, slots):
        active = [(i, s) for i, s in enumerate(slots) if s[0] > 0]
        if len(active) == 1 and active[0][0] == 0:
            mm, name, xpd = active[0][1]
            self.single_radio.setChecked(True)
            self.single_focal.setText(str(mm))
            self.single_name.setText(name)
            self.single_xpd.setValue(float(xpd))
        else:
            self.multi_radio.setChecked(True)
            self.single_focal.setText("")
            self.single_name.setText(DEFAULT_NAME)
            for i, (mm, name, xpd) in enumerate(slots):
                f, n, x = self.slot_focal[i], self.slot_name[i], self.slot_xpd[i]
                f.blockSignals(True)
                f.setText(str(mm) if mm else "")
                f.blockSignals(False)
                self._on_slot_focal(f, n, x)
                n.setText(name if mm else "")
                x.setValue(float(xpd) if mm else 45.0)
        self.update_mode()

    def on_new_lens_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self._t("Create EA9 lens TXT template", "新建 EA9 镜头 TXT 模板"),
            os.path.join(os.path.expanduser("~"), "ea9-lens-config.txt"),
            "EA9 lens configuration (*.txt)"
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            template = lens_config_txt.format_txt([(0, "", 0.0)] * 8, allow_empty=True)
            with open(path, "w", encoding="utf-8") as target:
                target.write(template)
            self._apply_lens_slots([(0, "", 0.0)] * 8)
            self.multi_radio.setChecked(True)
            self.status_lbl.setText(self._t(f"Created lens TXT template: {path}",
                                             f"已新建镜头 TXT 模板：{path}"))
        except Exception as e:
            QMessageBox.critical(self, self.windowTitle(), str(e))

    def on_load_lens_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self._t("Open EA9 lens TXT", "打开 EA9 镜头 TXT"),
            os.path.expanduser("~"), "EA9 lens configuration (*.txt);;Text files (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as source:
                slots = lens_config_txt.parse_txt(source.read())
            self._apply_lens_slots(slots)
            self.status_lbl.setText(self._t(f"Loaded lens configuration: {path}",
                                             f"已载入镜头配置：{path}"))
        except Exception as e:
            logging.exception("Lens TXT import failed")
            QMessageBox.warning(self, self.windowTitle(),
                self._t(f"Could not load lens TXT: {e}", f"无法读取镜头 TXT：{e}"))

    def on_save_lens_config(self):
        try:
            _, slots = self._collect_slots()
            content = lens_config_txt.format_txt(slots)
        except Exception as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self._t("Save EA9 lens TXT", "保存 EA9 镜头 TXT"),
            os.path.join(os.path.expanduser("~"), "ea9-lens-config.txt"),
            "EA9 lens configuration (*.txt)"
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as target:
                target.write(content)
            self.status_lbl.setText(self._t(f"Saved lens configuration: {path}",
                                             f"镜头配置已保存：{path}"))
        except OSError as e:
            QMessageBox.critical(self, self.windowTitle(), str(e))

    def _collect_slots(self):
        hw = int(self.hw_combo.currentText())
        single = self.single_radio.isChecked()
        rows = ([(self.single_focal, self.single_name, self.single_xpd, "")]
                if single else
                [(self.slot_focal[i], self.slot_name[i], self.slot_xpd[i],
                  f"{SLOT_APERTURES[i]} " + self._t("row: ", "行："))
                 for i in range(8)])
        slots = [(0, "", 0.0)] * 8
        for i, (fe, ne, xs, where) in enumerate(rows):
            ft = fe.text().strip()
            if not ft:
                continue
            if not ft.isdigit():
                raise ValueError(self._t(f"{where}Focal must be whole mm.", f"{where}焦距必须是整数。"))
            mm = int(ft)
            if mm == 0:
                continue
            if not (FOCAL_MIN <= mm <= FOCAL_MAX):
                raise ValueError(self._t(
                    f"{where}Focal must be {FOCAL_MIN}~{FOCAL_MAX} mm.",
                    f"{where}焦距必须在 {FOCAL_MIN}~{FOCAL_MAX} mm 之间。"
                ))
            name = ne.text().strip() or DEFAULT_NAME
            if len(name) > 63 or any(ord(c) < 0x20 or ord(c) > 0x7E for c in name):
                raise ValueError(self._t(
                    f"{where}Name must be <=63 printable ASCII chars.",
                    f"{where}镜头名称最多 63 字符，且只允许英文、数字和符号。"
                ))
            xs.interpretText()
            xpd = round(xs.value(), 1)
            if not (XPD_MIN <= xpd <= XPD_MAX):
                raise ValueError(self._t(
                    f"{where}Exit pupil must be {XPD_MIN}~{XPD_MAX} mm.",
                    f"{where}出瞳距离必须在 {XPD_MIN}~{XPD_MAX} mm 之间。"
                ))
            slots[i] = (mm, name, xpd)

        active = [i for i, (mm, _, _) in enumerate(slots) if mm > 0]
        if not active:
            raise ValueError(self._t("Enter at least one lens focal length.", "请至少配置一支镜头的焦距。"))
        if len(active) == 1 and active[0] != 0:
            slots[0] = slots[active[0]]
            slots[active[0]] = (0, "", 0.0)
        return hw, slots

    def on_write(self):
        if not self.device_ok or not self.seen_path or self.base_image is None:
            return
        try:
            hw, slots = self._collect_slots()
            # Patch the original updater's lens-calibration block into a copy of
            # our project TRACE image; never modify the bundled base image.
            block = cfgblock.pack(hw, slots)
            at = cfgblock.find(self.base_image)
            patched = bytearray(self.base_image)
            patched[at:at + cfgblock.SIZE] = block
            patched = bytes(patched)
            if patched[at:at + cfgblock.SIZE] != block:
                raise ValueError("Lens configuration block verification failed")
        except (ValueError, SystemExit) as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return

        digest = hashlib.sha256(patched).hexdigest()
        lines = [self._t(f"Adapter version: {hw}", f"转接环版本：{hw}")]
        active = [(i, s) for i, s in enumerate(slots) if s[0] > 0]
        if len(active) == 1:
            _, (mm, name, xpd) = active[0]
            lines.append(self._t(
                f'Single lens: {mm} mm, exit pupil {xpd:g} mm, "{name}"',
                f"单镜头：{mm} mm，出瞳 {xpd:g} mm，“{name}”"
            ))
        else:
            for i, (mm, name, xpd) in active:
                lines.append(self._t(
                    f'{SLOT_APERTURES[i]}: {mm} mm, exit pupil {xpd:g} mm, "{name}"',
                    f"{SLOT_APERTURES[i]}：{mm} mm，出瞳 {xpd:g} mm，“{name}”"
                ))
        prompt = self._t(
            "The project TRACE firmware will be flashed with these lens settings:\\n\\n"
            + "\\n".join(lines)
            + f"\\n\\nPatched image: {len(patched)} bytes\\nSHA-256: {digest}\\n"
            + "The application image is verified by readback. The separate TRACE log region is not touched.\\n"
            + "Make a full 128 KiB backup first. Continue?",
            "即将把以下镜头参数写入项目 TRACE 固件并烧录：\\n\\n"
            + "\\n".join(lines)
            + f"\\n\\n配置后镜像：{len(patched)} 字节\\nSHA-256：{digest}\\n"
            + "烧录后将逐字节回读校验应用区；独立 TRACE 日志区不会触碰。\\n"
            + "请先完成 128 KiB 完整 Flash 备份。确认继续？"
        )
        if QMessageBox.question(self, self.windowTitle(), prompt,
                                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        self.busy = True
        self.write_btn.setEnabled(False)
        self.backup_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        self.bar.setValue(0)
        self.worker = FlashWorker(self.seen_path, patched, self.declare_ver, self.zh)
        self.worker.progress.connect(lambda p, msg: (self.bar.setValue(p), self.status_lbl.setText(msg)))
        self.worker.finished.connect(self.on_done)
        self.worker.start()

    def on_done(self, ok, msg):
        self.busy = False
        if self.device_ok:
            self.backup_btn.setEnabled(True)
            self.restore_btn.setEnabled(True)
        self.status_lbl.setText(msg)
        if ok:
            self.bar.setValue(100)
            self.device_ok = False
            self.backup_btn.setEnabled(False)
            self.restore_btn.setEnabled(False)
            self.write_btn.setEnabled(False)
            self.device_lbl.setText(self._t("Firmware written", "固件已写入"))
            QMessageBox.information(self, self.windowTitle(), msg)
        else:
            self.seen_path = None
            QMessageBox.critical(self, self.windowTitle(), msg)

    def on_restore(self):
        if not self.device_ok or not self.seen_path or self.busy:
            return
        backup_path, _ = QFileDialog.getOpenFileName(
            self, self._t("Select a verified full-flash backup", "选择已保存的完整 Flash 备份"),
            os.path.expanduser("~"),
            "EA9 full-flash backup (*.bin);;All files (*)",
        )
        if not backup_path:
            return
        try:
            with open(backup_path, "rb") as source:
                backup = source.read()
            ea9_safety.validate_backup(backup)
            with open(backup_path + ".sha256", "r", encoding="ascii") as manifest:
                digest = ea9_safety.verify_sha256_manifest(backup, manifest.read())
            app_digest = hashlib.sha256(
                backup[ea9_safety.APP_BASE:ea9_safety.APP_END]
            ).hexdigest()
        except Exception as e:
            QMessageBox.critical(self, self.windowTitle(), self._t(
                f"Backup rejected: {e}", f"备份文件校验失败，已拒绝恢复：{e}"
            ))
            return
        prompt = self._t(
            "DANGER: This will replace the EA9 application area with the exact application bytes from this backup.\\n\\n"
            f"Backup SHA-256: {digest}\\nApplication SHA-256: {app_digest}\\n"
            "Before writing, the connected bootloader will be read twice and must match this backup exactly. "
            "Only 0x5000..0x15FFF is written. Bootloader 0x0000..0x4FFF and TRACE LOG from 0x16000 are excluded. "
            "Keep USB connected until byte-for-byte readback verification completes. Continue?",
            "危险操作：即将用此备份中的原始应用区字节完整替换 EA9 当前应用区。\\n\\n"
            f"完整备份 SHA-256：{digest}\\n应用区 SHA-256：{app_digest}\\n"
            "写入前会对当前 USB 引导区独立读取两次，并要求与此备份逐字节一致。"
            "本操作只写 0x5000..0x15FFF；不写引导区 0x0000..0x4FFF，也不写 0x16000 起的 TRACE 日志区。"
            "必须同时提供配套的 .sha256 校验文件。"
            "回读逐字节校验完成前请勿拔掉 USB。确认恢复？"
        )
        if QMessageBox.warning(
            self, self.windowTitle(), prompt,
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel
        ) != QMessageBox.Ok:
            return
        self.busy = True
        self.backup_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        self.write_btn.setEnabled(False)
        self.bar.setValue(0)
        self.restore_worker = RestoreWorker(
            self.seen_path, backup_path, self.declare_ver, self.zh
        )
        self.restore_worker.progress.connect(
            lambda pct, msg: (self.bar.setValue(pct), self.status_lbl.setText(msg))
        )
        self.restore_worker.finished.connect(self.on_restore_done)
        self.restore_worker.start()

    def on_restore_done(self, ok, msg):
        self.busy = False
        self.status_lbl.setText(msg)
        if self.device_ok:
            self.backup_btn.setEnabled(True)
            self.restore_btn.setEnabled(True)
            self.write_btn.setEnabled(True)
        if ok:
            self.bar.setValue(100)
            self.device_lbl.setText(self._t(
                "Original application restored; USB bootloader preserved.",
                "原应用区已恢复；USB 引导区保持不变。"
            ))
            QMessageBox.information(self, self.windowTitle(), msg)
        else:
            QMessageBox.critical(self, self.windowTitle(), msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
