#!/usr/bin/env python3
"""macOS GUI updater for Community EA9 firmware (0.5 mm XPD step, free range)."""

import os
import re
import sys
import hid
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDoubleSpinBox, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QTextEdit, QVBoxLayout, QWidget,
)

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
    EA9_BIN = os.path.join(BASE_DIR, "firmware", "ea9.bin")
    FW_CS = os.path.join(BASE_DIR, "win_src", "Firmware.cs")
    WIN_DOCS_DIR = os.path.join(BASE_DIR, "win_docs")
else:
    REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(REPO_DIR, "tools"))
    sys.path.insert(0, os.path.join(REPO_DIR, "tools", "win_updater"))
    EA9_BIN = os.path.join(REPO_DIR, "tools", "win_updater", "firmware", "ea9.bin")
    FW_CS = os.path.join(REPO_DIR, "tools", "win_updater", "src", "Firmware.cs")
    WIN_DOCS_DIR = os.path.join(REPO_DIR, "tools", "win_updater")

import cfgblock     # noqa: E402
import gen_optical  # noqa: E402

VID = 0x0483
PID = 0x575A
MAGIC = b"\x95\x27\x68\x18"
CMD_START = b"\x01\x01"
CMD_CHECK = b"\x02\x02"
REPORT_LEN = 64
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
            with HidBootloader(self.path) as dev:
                v = dev.check()
                msg0 = (f"已找到转接环（引导程序记录 {v[0]}.{v[1]}.{v[2]}），正在准备…"
                        if self.zh else
                        f"Found adapter (bootloader {v[0]}.{v[1]}.{v[2]}). Preparing...")
                self.progress.emit(0, msg0)
                dev.start(self.declare_ver)
                limit = (len(self.image) // 1024) * 1024 + 1024
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
            ok_msg = ("固件写入成功！现在请拔下 USB 线。"
                      if self.zh else
                      "The firmware was written successfully. Unplug the USB cable now.")
            self.finished.emit(True, ok_msg)
        except Exception as e:
            err_msg = f"写入失败：{e}" if self.zh else f"Writing failed: {e}"
            self.finished.emit(False, err_msg)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.declare_ver = load_declared_version()
        self.zh = True
        self.base_image = None
        self.seen_path = None
        self.device_ok = False
        self.busy = False
        self.worker = None
        self.boot_ver = None

        self.resize(820, 740)
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

        # 硬件版本
        hw_row = QHBoxLayout()
        self.hw_lbl = QLabel()
        self.hw_combo = QComboBox()
        self.hw_combo.addItems(["2", "1"])
        hw_row.addWidget(self.hw_lbl)
        hw_row.addWidget(self.hw_combo)
        hw_row.addStretch()
        lay.addLayout(hw_row)

        # 模式选择
        mode_row = QHBoxLayout()
        self.mode_lbl = QLabel()
        self.multi_radio = QRadioButton()
        self.single_radio = QRadioButton()
        self.multi_radio.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.multi_radio)
        bg.addButton(self.single_radio)
        self.multi_radio.toggled.connect(self.update_mode)
        mode_row.addWidget(self.mode_lbl)
        mode_row.addWidget(self.multi_radio)
        mode_row.addWidget(self.single_radio)
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
        self.single_name = QLineEdit(DEFAULT_NAME)
        self.single_name.setMaxLength(63)
        self.single_focal.textChanged.connect(lambda txt: self._auto_xpd(txt, self.single_xpd))
        sg.addWidget(self.s_focal_lbl, 0, 0)
        sg.addWidget(self.single_focal, 0, 1)
        sg.addWidget(self.s_xpd_lbl, 1, 0)
        sg.addWidget(self.single_xpd, 1, 1)
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

        # 设备与进度条
        self.device_lbl = QLabel()
        lay.addWidget(self.device_lbl)

        btn_row = QHBoxLayout()
        self.write_btn = QPushButton()
        self.write_btn.setEnabled(False)
        self.write_btn.clicked.connect(self.on_write)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        btn_row.addWidget(self.write_btn)
        btn_row.addWidget(self.bar)
        lay.addLayout(btn_row)

        self.status_lbl = QLabel()
        lay.addWidget(self.status_lbl)

        try:
            with open(EA9_BIN, "rb") as bf:
                self.base_image = bf.read()
            cfgblock.find(self.base_image)
        except Exception as e:
            self.base_image = None
            self.status_lbl.setText(f"无法读取内置固件 ea9.bin：{e}")

        self.apply_text()
        self.update_mode()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_usb)
        self.poll_timer.start(2000)
        self.poll_usb()

    def _t(self, en, zh):
        return zh if self.zh else en

    def on_lang_changed(self, idx):
        self.zh = (idx == 0)
        self.apply_text()

    def apply_text(self):
        v = self.declare_ver
        self.setWindowTitle(
            self._t(
                f"Community EA9 Mac Firmware Updater: v{v[0]}.{v[1]}.{v[2]} (0.5mm XPD Mod)",
                f"Community EA9 固件更新工具 (Mac)：v{v[0]}.{v[1]}.{v[2]} (0.5mm XPD Mod)",
            )
        )
        self.notes_lbl.setText(self._t("Release notes", "更新说明"))
        self.notes_box.setPlainText(load_changelog(self.zh))
        self.hw_lbl.setText(self._t("Adapter version:", "转接环版本："))
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
        self.write_btn.setText(self._t("Write firmware", "写入固件"))
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
        if path is None:
            self._refresh_status_text()
            return
        try:
            with HidBootloader(path) as dev:
                self.boot_ver = dev.check()
            self.device_ok = True
            self._refresh_status_text()
            self.write_btn.setEnabled(True)
        except Exception as e:
            self.device_lbl.setText(self._t("Adapter: Not responding", "转接环：无响应"))
            self.status_lbl.setText(self._t(f"No answer ({e})", f"转接环没有响应（{e}）"))

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
        except ValueError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return

        lines = [self._t(f"Adapter version: {hw}", f"转接环版本：{hw}")]
        active = [(i, s) for i, s in enumerate(slots) if s[0] > 0]
        if len(active) == 1:
            _, (mm, name, xpd) = active[0]
            lines.append(self._t(
                f"Single lens: {mm} mm, exit pupil {xpd:g} mm, \"{name}\"",
                f"单镜头：{mm} mm，出瞳 {xpd:g} mm，“{name}”"
            ))
        else:
            for i, (mm, name, xpd) in active:
                lines.append(self._t(
                    f"{SLOT_APERTURES[i]}: {mm} mm, exit pupil {xpd:g} mm, \"{name}\"",
                    f"{SLOT_APERTURES[i]}：{mm} mm，出瞳 {xpd:g} mm，“{name}”"
                ))

        prompt = self._t(
            "The firmware will now be written with:\n\n" + "\n".join(lines) + "\n\nDo not unplug USB until finished.",
            "即将按以下配置把固件写入转接环：\n\n" + "\n".join(lines) + "\n\n写入完成前，请勿拔下 USB 线！"
        )
        if QMessageBox.question(self, self.windowTitle(), prompt, QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return

        block = cfgblock.pack(hw, slots)
        at = cfgblock.find(self.base_image)
        patched = bytearray(self.base_image)
        patched[at:at + cfgblock.SIZE] = block

        self.busy = True
        self.write_btn.setEnabled(False)
        self.bar.setValue(0)
        self.worker = FlashWorker(self.seen_path, bytes(patched), self.declare_ver, self.zh)
        self.worker.progress.connect(lambda p, msg: (self.bar.setValue(p), self.status_lbl.setText(msg)))
        self.worker.finished.connect(self.on_done)
        self.worker.start()

    def on_done(self, ok, msg):
        self.busy = False
        self.status_lbl.setText(msg)
        if ok:
            self.bar.setValue(100)
            self.device_ok = False
            self.device_lbl.setText(self._t("Firmware written", "固件已写入"))
            QMessageBox.information(self, self.windowTitle(), msg)
        else:
            self.seen_path = None
            QMessageBox.critical(self, self.windowTitle(), msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
