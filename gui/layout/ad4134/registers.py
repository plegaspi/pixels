from __future__ import annotations

import threading
from pathlib import Path

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_REGISTERS_YAML = (
    Path(__file__).parent.parent.parent.parent / "control" / "registers.yaml"
)

_DEFAULT_ACTIVE_CS = [
    {"cs": 0, "label": "AD4134 / CN0561"},
    {"cs": 1, "label": "AD7134 #1"},
    {"cs": 2, "label": "AD7134 #2"},
]

# Fixed color scheme per category: (bg_none, border_none, bg_0, border_0, bg_1, border_1)
_GRAY = ("#2e2e2e", "#484848")  # shared no-value bg/border
_BIT_COLORS = {
    #              no-value                value=0                  value=1
    "reserved": (*_GRAY, "#3d1a5c", "#5a2a80", "#7a35b5", "#9a50d0"),
    "read_only": (*_GRAY, "#1a2a4a", "#2a4070", "#1a4a9a", "#2060cc"),
    "writable": (*_GRAY, "#4a1a1a", "#801a1a", "#1a4a1a", "#1a7a1a"),
}
_BIT_FG = {
    #              no-value    0           1
    "reserved": ("#666666", "#8844aa", "#e0b8ff"),
    "read_only": ("#666666", "#4a6a9a", "#a0c8ff"),
    "writable": ("#666666", "#cc4444", "#44cc44"),
}


def _parse_bits(bits_dict: dict) -> dict[int, str]:
    """Return {bit_position: field_name} for every bit 0-7."""
    result: dict[int, str] = {}
    for key, name in bits_dict.items():
        s = str(key)
        if "-" in s:
            hi, lo = (int(x) for x in s.split("-"))
            for b in range(lo, hi + 1):
                result[b] = str(name)
        else:
            result[int(s)] = str(name)
    return result


def _load_registers() -> list[dict]:
    with open(_REGISTERS_YAML) as fh:
        return yaml.safe_load(fh)["registers"]


def _active_cs_from_cfg(cfg: dict | None) -> list[dict]:
    entries = (
        (cfg or {})
        .get("adx134", {})
        .get("registers", {})
        .get("active_cs", _DEFAULT_ACTIVE_CS)
    )
    if not isinstance(entries, (list, tuple)):
        entries = _DEFAULT_ACTIVE_CS
    result: list[dict] = []
    seen: set[int] = set()
    for entry in entries:
        try:
            cs = int(entry.get("cs"))
        except (AttributeError, TypeError, ValueError):
            continue
        if not 0 <= cs <= 31 or cs in seen:
            continue
        label = str(entry.get("label") or f"CS {cs}")
        result.append({"cs": cs, "label": label})
        seen.add(cs)
    return result or list(_DEFAULT_ACTIVE_CS)


class _BitCell(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class _BitWidget(QWidget):
    """Eight colored bit-cells with per-bit field tooltips."""

    bit_toggled = Signal(int)  # bit index 0-7

    def __init__(self, bit_map: dict[int, str], is_writable: bool = False):
        super().__init__()
        self._bit_map = bit_map
        self._is_writable = is_writable
        self._current_value: int | None = None

        lay = QHBoxLayout()
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)

        self._cells: list[_BitCell] = []
        for bit in range(7, -1, -1):
            cell = _BitCell("—")
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.setFixedSize(20, 20)
            field = bit_map.get(bit, "—")
            cell.setToolTip(f"Bit {bit}: {field}")
            self._apply_style(cell, bit, value=None)
            if is_writable and field != "Reserved":
                cell.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                cell.clicked.connect(lambda b=bit: self._on_cell_clicked(b))
            lay.addWidget(cell)
            self._cells.append(cell)

        lay.addStretch()
        self.setLayout(lay)

    def _apply_style(self, cell: QLabel, bit: int, value: int | None) -> None:
        field = self._bit_map.get(bit, "—")
        if field == "Reserved":
            cat = "reserved"
        elif self._is_writable:
            cat = "writable"
        else:
            cat = "read_only"

        cols = _BIT_COLORS[cat]  # bg_none, border_none, bg_0, border_0, bg_1, border_1
        fgs = _BIT_FG[cat]  # fg_none, fg_0, fg_1

        if value is None:
            bg, border, fg, text = cols[0], cols[1], fgs[0], "—"
        else:
            bit_val = (value >> bit) & 1
            if bit_val:
                bg, border, fg, text = cols[4], cols[5], fgs[2], "1"
            else:
                bg, border, fg, text = cols[2], cols[3], fgs[1], "0"
        cell.setText(text)
        cell.setStyleSheet(
            f"QLabel {{ background-color: {bg}; border: 1px solid {border}; "
            "border-radius: 2px; font-size: 8px; font-weight: bold; "
            f"color: {fg}; }}"
            "QToolTip { color: #000000; background-color: #ffffff; "
            "border: 1px solid #000000; font-size: 12px; font-weight: normal; padding: 4px; }"
        )

    def set_value(self, value: int | None) -> None:
        self._current_value = value
        for i, cell in enumerate(self._cells):
            bit = 7 - i
            self._apply_style(cell, bit, value)

    def _on_cell_clicked(self, bit: int) -> None:
        current = self._current_value if self._current_value is not None else 0
        new_val = current ^ (1 << bit)
        self.set_value(new_val)
        self.bit_toggled.emit(bit)


class ADX134RegistersTab(QWidget):
    _reg_read = Signal(int, int, int)  # cs_sel, reg_addr, value
    _read_done = Signal(str)  # "" on success, error message on failure
    _write_done = Signal(str)  # "" on success, error message on failure

    def __init__(self):
        super().__init__()
        app = QApplication.instance()
        if app:
            palette = app.palette()
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
            app.setPalette(palette)
        self._registers = _load_registers()
        self._active_cs = list(_DEFAULT_ACTIVE_CS)
        # Per-device register values: cs -> {reg_addr: int | None}
        self._values: dict[int, dict[int, int | None]] = {
            cs: {reg["reg"]: None for reg in self._registers}
            for cs in [entry["cs"] for entry in self._active_cs]
        }
        self._current_cs = self._active_cs[0]["cs"]
        self._bit_widgets: list[_BitWidget] = []
        self._conn = None
        self._file_values: dict[int, int | None] = {}  # addr -> val from loaded YAML
        # Last successfully written values per device, for change detection
        self._written_values: dict[int, dict[int, int]] = {
            cs: {} for cs in [entry["cs"] for entry in self._active_cs]
        }

        outer = QVBoxLayout()
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Top bar ───────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("ADC:"))
        self._adc_sel = QComboBox()
        self._populate_adc_selector()
        top.addWidget(self._adc_sel)
        top.addSpacing(12)
        for label, bg, fg in [
            ("Reserved", _BIT_COLORS["reserved"][4], _BIT_FG["reserved"][2]),
            ("Read-only", _BIT_COLORS["read_only"][4], _BIT_FG["read_only"][2]),
            ("W = 0", _BIT_COLORS["writable"][2], _BIT_FG["writable"][1]),
            ("W = 1", _BIT_COLORS["writable"][4], _BIT_FG["writable"][2]),
        ]:
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {bg}; border: 1px solid {fg}; border-radius: 2px;"
            )
            txt = QLabel(label)
            txt.setStyleSheet("color: #000000; font-size: 11px;")
            top.addWidget(swatch)
            top.addWidget(txt)
            top.addSpacing(6)
        top.addStretch()
        self._read_btn = QPushButton("Read All")
        self._write_btn = QPushButton("Write All")
        self._load_btn = QPushButton("Load YAML…")
        self._save_btn = QPushButton("Save to YAML…")
        top.addWidget(self._read_btn)
        top.addWidget(self._write_btn)
        top.addWidget(self._load_btn)
        top.addWidget(self._save_btn)
        outer.addLayout(top)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["Addr", "Register Name", "R/W", "Reset", "Value", "File", "Bits  [7 ▸ 0]"]
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(6, 190)

        self._build_rows()
        outer.addWidget(self._table)
        self.setLayout(outer)

        self._adc_sel.currentIndexChanged.connect(self._on_adc_changed)
        self._read_btn.clicked.connect(self._on_read_all)
        self._write_btn.clicked.connect(self._on_write_all)
        self._load_btn.clicked.connect(self._on_load_yaml)
        self._save_btn.clicked.connect(self._on_save)
        self._reg_read.connect(self._on_reg_read)
        self._read_done.connect(self._on_read_all_done)
        self._write_done.connect(self._on_write_all_done)
        self._table.itemChanged.connect(self._on_value_edited)

    def _build_rows(self) -> None:
        self._table.setRowCount(len(self._registers))
        self._bit_widgets.clear()

        def _mk(text: str, align=Qt.AlignmentFlag.AlignCenter) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return item

        for row, reg in enumerate(self._registers):
            addr: int = reg["reg"]
            reset = reg.get("reset", "N/A")
            reset_str = f"0x{reset:02X}" if isinstance(reset, int) else str(reset)
            bit_map = _parse_bits(reg.get("bits", {}))

            self._table.setItem(row, 0, _mk(f"0x{addr:02X}"))
            self._table.setItem(
                row,
                1,
                _mk(
                    reg.get("name", ""),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                ),
            )
            self._table.setItem(row, 2, _mk(str(reg.get("rw", ""))))
            self._table.setItem(row, 3, _mk(reset_str))
            rw = str(reg.get("rw", ""))
            is_writable = "W" in rw
            val_item = _mk("—")
            if is_writable:
                val_item.setFlags(val_item.flags() | Qt.ItemFlag.ItemIsEditable)
                val_item.setToolTip("Double-click to edit (hex: 0xFF or FF)")
            self._table.setItem(row, 4, val_item)
            self._table.setItem(row, 5, _mk("—"))

            bw = _BitWidget(bit_map, is_writable=is_writable)
            bw.bit_toggled.connect(lambda bit, r=row: self._on_bit_toggled(r, bit))
            self._table.setCellWidget(row, 6, bw)
            self._table.setRowHeight(row, 28)
            self._bit_widgets.append(bw)

    def _refresh_values(self) -> None:
        self._ensure_cs_state(self._current_cs)
        vals = self._values[self._current_cs]
        self._table.blockSignals(True)
        for row, reg in enumerate(self._registers):
            addr = reg["reg"]
            v = vals.get(addr)
            self._table.item(row, 4).setText(f"0x{v:02X}" if v is not None else "—")
            fv = self._file_values.get(addr)
            self._table.item(row, 5).setText(f"0x{fv:02X}" if fv is not None else "—")
            self._bit_widgets[row].set_value(v)
        self._table.blockSignals(False)

    def _on_adc_changed(self, index: int) -> None:
        cs = self._adc_sel.itemData(index)
        if cs is None:
            return
        self._current_cs = int(cs)
        self._ensure_cs_state(self._current_cs)
        self._refresh_values()

    def _ensure_cs_state(self, cs: int) -> None:
        if cs not in self._values:
            self._values[cs] = {reg["reg"]: None for reg in self._registers}
        if cs not in self._written_values:
            self._written_values[cs] = {}

    def _populate_adc_selector(self) -> None:
        previous = self._current_cs if hasattr(self, "_current_cs") else None
        self._adc_sel.blockSignals(True)
        self._adc_sel.clear()
        for entry in self._active_cs:
            cs = int(entry["cs"])
            self._adc_sel.addItem(f"{entry['label']} (cs={cs})", cs)
            self._ensure_cs_state(cs)
        idx = self._adc_sel.findData(previous)
        self._adc_sel.setCurrentIndex(idx if idx >= 0 else 0)
        current = self._adc_sel.currentData()
        if current is not None:
            self._current_cs = int(current)
        self._adc_sel.blockSignals(False)

    def load_from_cfg(self, cfg: dict) -> None:
        self._active_cs = _active_cs_from_cfg(cfg)
        self._populate_adc_selector()
        self._refresh_values()

    def set_connection(self, conn) -> None:
        self._conn = conn

    def set_tx_active(self, active: bool) -> None:
        blocked = active
        self._read_btn.setEnabled(not blocked)
        self._write_btn.setEnabled(not blocked)
        tip = "Stop the ADC stream before reading/writing registers." if blocked else ""
        self._read_btn.setToolTip(tip)
        self._write_btn.setToolTip(tip)

    def _on_read_all(self) -> None:
        if self._conn is None:
            QMessageBox.warning(
                self, "Not Connected", "Configure the board before reading registers."
            )
            return
        self._read_btn.setEnabled(False)
        self._write_btn.setEnabled(False)
        threading.Thread(
            target=self._run_read_all,
            args=(self._current_cs, self._conn, list(self._registers)),
            daemon=True,
        ).start()

    def _run_read_all(self, cs: int, conn, regs: list) -> None:
        from pixels.control.spi import spi_reg_rd, open_response_socket

        try:
            with open_response_socket(conn) as sock:
                sock.settimeout(2.0)
                for reg in regs:
                    addr = reg["reg"]
                    val = spi_reg_rd(
                        addr, cs, conn, timeout=2.0, verbose=False, sock=sock
                    )
                    if val is None:
                        self._read_done.emit(
                            "Timeout — no response (is the ADC stream stopped?)"
                        )
                        return
                    self._reg_read.emit(cs, addr, val)
            self._read_done.emit("")
        except Exception as exc:
            self._read_done.emit(str(exc))

    def _on_reg_read(self, cs: int, addr: int, val: int) -> None:
        self._ensure_cs_state(cs)
        self._values[cs][addr] = val
        if cs != self._current_cs:
            return
        for row, reg in enumerate(self._registers):
            if reg["reg"] == addr:
                self._table.blockSignals(True)
                self._table.item(row, 4).setText(f"0x{val:02X}")
                self._table.blockSignals(False)
                self._bit_widgets[row].set_value(val)
                break

    def _on_value_edited(self, item: QTableWidgetItem) -> None:
        if item.column() != 4:
            return
        row = item.row()
        reg = self._registers[row]
        text = item.text().strip()
        try:
            val = (
                int(text, 16)
                if text.startswith("0x") or text.startswith("0X")
                else int(text, 16)
            )
            val = max(0, min(0xFF, val))
        except ValueError:
            # Revert to last known value
            v = self._values[self._current_cs].get(reg["reg"])
            self._table.blockSignals(True)
            item.setText(f"0x{v:02X}" if v is not None else "—")
            self._table.blockSignals(False)
            return
        self._values[self._current_cs][reg["reg"]] = val
        self._table.blockSignals(True)
        item.setText(f"0x{val:02X}")
        self._table.blockSignals(False)
        self._bit_widgets[row].set_value(val)

    def _on_bit_toggled(self, row: int, bit: int) -> None:
        reg = self._registers[row]
        bw = self._bit_widgets[row]
        new_val = bw._current_value if bw._current_value is not None else 0
        self._values[self._current_cs][reg["reg"]] = new_val
        self._table.blockSignals(True)
        self._table.item(row, 4).setText(f"0x{new_val:02X}")
        self._table.blockSignals(False)

    def _on_read_all_done(self, err: str) -> None:
        self._read_btn.setEnabled(True)
        self._write_btn.setEnabled(True)
        if err:
            QMessageBox.warning(self, "Read Failed", err)

    def _on_write_all(self) -> None:
        if self._conn is None:
            QMessageBox.warning(
                self, "Not Connected", "Configure the board before writing registers."
            )
            return
        vals = self._values[self._current_cs]
        written = self._written_values[self._current_cs]
        to_write = [
            (reg, vals[reg["reg"]])
            for reg in self._registers
            if "W" in str(reg.get("rw", ""))
            and vals.get(reg["reg"]) is not None
            and vals[reg["reg"]] != written.get(reg["reg"])
        ]
        if not to_write:
            QMessageBox.information(
                self, "Nothing to Write", "No writable registers have values set."
            )
            return
        self._read_btn.setEnabled(False)
        self._write_btn.setEnabled(False)
        threading.Thread(
            target=self._run_write_all,
            args=(self._current_cs, self._conn, to_write),
            daemon=True,
        ).start()

    def _run_write_all(self, cs: int, conn, to_write: list) -> None:
        from pixels.control.spi import spi_reg_wr
        import time

        try:
            for reg, val in to_write:
                spi_reg_wr(reg["reg"], val, cs, conn, verbose=False)
                self._written_values[cs][reg["reg"]] = val
                time.sleep(0.010)
            self._write_done.emit("")
        except Exception as exc:
            self._write_done.emit(str(exc))

    def _on_write_all_done(self, err: str) -> None:
        self._read_btn.setEnabled(True)
        self._write_btn.setEnabled(True)
        if err:
            QMessageBox.warning(self, "Write Failed", err)

    def _on_load_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ADC Registers", "", "YAML files (*.yaml *.yml)"
        )
        if not path:
            return
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
            regs = data.get("registers", {})

            def _parse(v):
                if v is None:
                    return None
                return v if isinstance(v, int) else int(v, 16)

            self._file_values = {
                _parse(addr): _parse(val) for addr, val in regs.items()
            }
        except Exception as exc:
            QMessageBox.warning(self, "Load Failed", str(exc))
            return
        for row, reg in enumerate(self._registers):
            fv = self._file_values.get(reg["reg"])
            self._table.item(row, 5).setText(f"0x{fv:02X}" if fv is not None else "—")

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ADC Registers", "", "YAML files (*.yaml *.yml)"
        )
        if not path:
            return
        vals = self._values[self._current_cs]
        out = {
            "adc": self._adc_sel.currentText().strip(),
            "registers": {
                f"0x{reg['reg']:02X}": (
                    f"0x{vals[reg['reg']]:02X}"
                    if vals.get(reg["reg"]) is not None
                    else None
                )
                for reg in self._registers
            },
        }
        with open(path, "w") as fh:
            yaml.dump(out, fh, default_flow_style=False, sort_keys=False)

    def load_register_values(self, cs: int, values: dict[int, int]) -> None:
        """External hook: populate values for a device after a real read."""
        self._ensure_cs_state(cs)
        self._values[cs].update(values)
        if cs == self._current_cs:
            self._refresh_values()
