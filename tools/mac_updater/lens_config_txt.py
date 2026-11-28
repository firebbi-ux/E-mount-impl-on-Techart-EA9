"""Human-readable EA9 lens-list TXT format and conversion to firmware slots."""
import re
import cfgblock

FLASH_APP_START = 0x5000
FLASH_APP_END = 0x16000
PREFERRED_HW_VERSION = 2
SLOT_APERTURES = ("f/2.5", "f/2.8", "f/3.2", "f/3.5",
                  "f/4.0", "f/4.5", "f/5.0", "f/5.6")
FIELDS = ("槽位号", "焦距", "镜头名称", "出瞳距离")


def _validate_slots(slots, allow_empty=False):
    if len(slots) != 8:
        raise ValueError("镜头配置必须包含 8 个槽位")
    clean = []
    for i, item in enumerate(slots):
        mm, name, xpd = item
        if not mm:
            if name or xpd:
                raise ValueError("槽位 %d 未设置焦距，其他字段必须留空" % (i + 1))
            clean.append((0, "", 0.0))
            continue
        if not 8 <= int(mm) <= 600:
            raise ValueError("槽位 %d 焦距必须为 8–600 mm" % (i + 1))
        name = str(name).strip()
        try:
            raw = name.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("槽位 %d 镜头名称只支持 ASCII 字符" % (i + 1))
        if not raw or len(raw) > 63 or any(b < 0x20 or b > 0x7e for b in raw):
            raise ValueError("槽位 %d 镜头名称必须为 1–63 个可打印 ASCII 字符" % (i + 1))
        xpd = round(float(xpd), 1)
        if not 1.0 <= xpd <= 1000.0:
            raise ValueError("槽位 %d 出瞳距离必须为 1.0–1000.0 mm" % (i + 1))
        clean.append((int(mm), name, xpd))
    if not allow_empty and not any(x[0] for x in clean):
        raise ValueError("至少需要配置一支镜头")
    return clean


def format_txt(slots, allow_empty=True):
    slots = _validate_slots(slots, allow_empty=allow_empty)
    lines = ["EA9 镜头配置", "单位：焦距 mm；出瞳距离 mm", ""]
    for i, (mm, name, xpd) in enumerate(slots):
        lines.extend([
            "槽位号：%d（机身切换镜头光圈档位：%s）" % (i + 1, SLOT_APERTURES[i]),
            "焦距：%s" % (mm if mm else ""),
            "镜头名称：%s" % name,
            "出瞳距离：%s" % (("%.1f" % xpd) if mm else ""),
            ""
        ])
    return "\n".join(lines)


def parse_txt(text):
    # Deliberately plain labelled lines: no scripting, expressions or hidden data.
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    slots = [None] * 8
    current = None
    values = {}
    def finish():
        nonlocal values, current
        if current is None:
            return
        if slots[current] is not None:
            raise ValueError("槽位 %d 重复" % (current + 1))
        focal = values.get("焦距", "")
        name = values.get("镜头名称", "")
        xpd = values.get("出瞳距离", "")
        if not focal and not name and not xpd:
            slots[current] = (0, "", 0.0)
        elif not focal or not name or not xpd:
            raise ValueError("槽位 %d 的焦距、镜头名称和出瞳距离必须同时填写" % (current + 1))
        else:
            if not re.fullmatch(r"\d+", focal):
                raise ValueError("槽位 %d 焦距格式错误" % (current + 1))
            if not re.fullmatch(r"\d+(?:\.\d)?", xpd):
                raise ValueError("槽位 %d 出瞳距离格式错误" % (current + 1))
            slots[current] = (int(focal), name, float(xpd))
        values = {}
    for line in lines:
        m = re.fullmatch(r"槽位号：\s*(\d+)（机身切换镜头光圈档位：([^）]+)）", line)
        if m:
            finish()
            current = int(m.group(1)) - 1
            if not 0 <= current < 8 or m.group(2) != SLOT_APERTURES[current]:
                raise ValueError("槽位号与机身切换镜头光圈档位不匹配")
            values = {}
            continue
        if current is not None:
            m = re.fullmatch(r"(焦距|镜头名称|出瞳距离)：\s*(.*)", line)
            if m:
                key, value = m.groups()
                if key in values:
                    raise ValueError("槽位 %d 字段重复：%s" % (current + 1, key))
                values[key] = value
    finish()
    if any(x is None for x in slots):
        raise ValueError("TXT 必须按标准格式包含完整的 8 个槽位")
    return _validate_slots(slots, allow_empty=True)


def slots_from_flash(flash, preferred_hw_version=PREFERRED_HW_VERSION):
    """Extract the valid lens block from the application area of a full-flash dump.

    A device backup can contain more than one historical CfgAnchor block.
    Validate each candidate structurally and against the optical-file generator,
    then prefer the selected firmware hardware version (v2 by default).
    Never scan the bootloader or TRACE log region.
    """
    if len(flash) != 0x20000:
        raise ValueError("完整 Flash 备份必须为 128 KiB")

    candidates = []
    search_at = FLASH_APP_START
    while True:
        at = flash.find(cfgblock.MAGIC, search_at, FLASH_APP_END)
        if at < 0:
            break
        search_at = at + 1
        if at + cfgblock.SIZE > FLASH_APP_END:
            continue
        block = flash[at:at + cfgblock.SIZE]
        try:
            hw, slots = cfgblock.parse(block)
            slots = _validate_slots(slots)
        except (SystemExit, ValueError, UnicodeError) as error:
            continue
        candidates.append((at, hw, slots))

    if not candidates:
        raise ValueError(
            "应用区 0x%05X..0x%05X 未找到通过完整校验的镜头配置块"
            % (FLASH_APP_START, FLASH_APP_END)
        )

    preferred = [item for item in candidates if item[1] == preferred_hw_version]
    if len(preferred) == 1:
        return preferred[0][2]
    if len(preferred) > 1:
        offsets = ", ".join("0x%05X" % item[0] for item in preferred)
        raise ValueError(
            "应用区存在多个硬件版本 %d 的有效镜头配置块：%s"
            % (preferred_hw_version, offsets)
        )
    if len(candidates) == 1:
        return candidates[0][2]

    details = ", ".join("0x%05X(v%d)" % (at, hw) for at, hw, _ in candidates)
    raise ValueError("发现多个有效镜头配置块，无法确定当前配置：" + details)
