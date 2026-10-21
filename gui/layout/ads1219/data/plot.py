from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixels.daq.ads1219.constants import LSB, MUX_LABELS

_DEFAULT_LIVE_WINDOW = 200
_DEFAULT_LOAD_WINDOW = 1_000

_CHANNEL_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2",
]


class ADS1219PlotTab(QWidget):
    def __init__(self):
        super().__init__()
        self._adc = None
        self._load_path: str | None = None
        self._load_start: int | None = None   # None = latest
        self._plot_mode = "live"
        self._display_mode = "voltage"
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._visible_channels: set[int] = set(range(7))

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        # ── Plot ──────────────────────────────────────────────────────────────
        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "Sample index")
        self._plot.setLabel("left", "Voltage", units="V")

        # ── Live controls ─────────────────────────────────────────────────────
        self._live_win_spin = QSpinBox()
        self._live_win_spin.setRange(10, 1_000_000)
        self._live_win_spin.setValue(_DEFAULT_LIVE_WINDOW)
        self._live_win_spin.setFixedWidth(130)
        self._live_win_spin.setSuffix(" samples")

        self._autoscale_btn = QPushButton("Autoscale")
        self._autoscale_btn.clicked.connect(self._plot.enableAutoRange)

        self._status_label = QLabel("No data")
        self._status_label.setStyleSheet("color: #888;")

        live_ctrl = QHBoxLayout()
        live_ctrl.addWidget(QLabel("Window:"))
        live_ctrl.addWidget(self._live_win_spin)
        live_ctrl.addWidget(self._autoscale_btn)
        live_ctrl.addStretch()
        live_ctrl.addWidget(self._status_label)

        self._live_controls = QWidget()
        self._live_controls.setLayout(live_ctrl)

        # ── Load controls ─────────────────────────────────────────────────────
        self._btn_first   = QPushButton("<<")
        self._btn_prev    = QPushButton("<")
        self._start_spin  = QSpinBox()
        self._start_spin.setRange(0, 100_000_000)
        self._start_spin.setFixedWidth(120)
        self._btn_go      = QPushButton("Go")
        self._btn_next    = QPushButton(">")
        self._btn_last    = QPushButton(">>")
        self._btn_latest  = QPushButton("Latest")
        self._load_win_spin = QSpinBox()
        self._load_win_spin.setRange(10, 1_000_000)
        self._load_win_spin.setValue(_DEFAULT_LOAD_WINDOW)
        self._load_win_spin.setFixedWidth(130)
        self._load_win_spin.setSuffix(" samples")
        self._lbl_total   = QLabel("")
        self._lbl_total.setStyleSheet("color: #888;")
        self._load_autoscale_btn = QPushButton("Autoscale")
        self._load_autoscale_btn.clicked.connect(self._plot.enableAutoRange)

        load_nav = QHBoxLayout()
        for w in (self._btn_first, self._btn_prev):
            load_nav.addWidget(w)
        load_nav.addWidget(QLabel("Start:"))
        load_nav.addWidget(self._start_spin)
        for w in (self._btn_go, self._btn_next, self._btn_last, self._btn_latest):
            load_nav.addWidget(w)
        load_nav.addWidget(QLabel("  Window:"))
        load_nav.addWidget(self._load_win_spin)
        load_nav.addWidget(self._load_autoscale_btn)
        load_nav.addStretch()
        load_nav.addWidget(self._lbl_total)

        self._load_controls = QWidget()
        self._load_controls.setLayout(load_nav)
        self._load_controls.setVisible(False)

        layout.addWidget(self._live_controls)
        layout.addWidget(self._load_controls)
        layout.addWidget(self._plot)
        self.setLayout(layout)

        # ── Wire load navigation ───────────────────────────────────────────────
        self._btn_first.clicked.connect(self._go_first)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_go.clicked.connect(self._go_to_spin)
        self._start_spin.editingFinished.connect(self._go_to_spin)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_last.clicked.connect(self._go_last)
        self._btn_latest.clicked.connect(self._go_latest)
        self._load_win_spin.editingFinished.connect(self._load_refresh)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh_live)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_plot_mode(self, mode: str) -> None:
        self._plot_mode = mode
        self._live_controls.setVisible(mode == "live")
        self._load_controls.setVisible(mode == "load")
        if mode == "live":
            if self._adc is not None:
                self._timer.start()
        elif mode == "load":
            self._timer.stop()
            if self._load_path:
                self._load_refresh()
        else:
            self._timer.stop()
            for c in self._curves.values():
                c.setData([], [])
            self._status_label.setText("Plotting disabled")

    def set_visible_channels(self, channels: list[int]) -> None:
        self._visible_channels = set(channels)
        for mux, curve in self._curves.items():
            curve.setVisible(mux in self._visible_channels)
        if self._load_path and self._plot_mode == "load":
            self._load_refresh()

    def set_display_mode(self, mode: str) -> None:
        self._display_mode = mode
        label = "Voltage" if mode == "voltage" else "Raw counts"
        units = "V" if mode == "voltage" else ""
        self._plot.setLabel("left", label, units=units)
        if self._load_path and self._plot_mode == "load":
            self._load_refresh()
        elif self._plot_mode == "live" and self._adc is not None:
            self._refresh_live()

    def start(self, adc) -> None:
        self._adc = adc
        self._curves.clear()
        self._plot.clear()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._adc = None

    def load_file(self, path: str) -> None:
        self._load_path = path
        self._load_start = None
        self._plot.clear()
        self._curves.clear()
        self._load_refresh()

    # ── Internal: load navigation ─────────────────────────────────────────────

    def _total_load_samples(self) -> int:
        if not self._load_path:
            return 0
        from pixels.models import ADS1219Data
        from pathlib import Path
        try:
            with ADS1219Data(Path(self._load_path)) as d:
                chs = d.channels
                return min(ch.n_samples for ch in chs.values()) if chs else 0
        except Exception:
            return 0

    def _go_first(self) -> None:
        self._load_start = 0
        self._load_refresh()

    def _go_last(self) -> None:
        self._load_start = None
        self._load_refresh()

    def _go_latest(self) -> None:
        self._load_start = None
        self._load_refresh()

    def _go_prev(self) -> None:
        n = self._load_win_spin.value()
        if self._load_start is None:
            self._load_start = max(0, self._total_load_samples() - n)
        self._load_start = max(0, self._load_start - n)
        self._load_refresh()

    def _go_next(self) -> None:
        if self._load_start is None:
            return
        n = self._load_win_spin.value()
        total = self._total_load_samples()
        nxt = self._load_start + n
        self._load_start = None if nxt >= total - n else nxt
        self._load_refresh()

    def _go_to_spin(self) -> None:
        self._load_start = self._start_spin.value()
        self._load_refresh()

    def _load_refresh(self) -> None:
        if not self._load_path:
            return
        from pixels.models import ADS1219Data
        from pathlib import Path
        try:
            n = self._load_win_spin.value()
            with ADS1219Data(Path(self._load_path)) as d:
                chs = d.channels
                if not chs:
                    return
                total = min(ch.n_samples for ch in chs.values())
                self._lbl_total.setText(f"{total:,} samples total")
                actual_start = max(0, total - n) if self._load_start is None \
                    else max(0, min(self._load_start, total - 1))
                stop = min(actual_start + n, total)

                self._plot.clear()
                loaded = 0
                for mux, ch in chs.items():
                    if mux not in self._visible_channels:
                        continue
                    t = np.arange(actual_start, stop)
                    y = ch.voltage[actual_start:stop] if self._display_mode == "voltage" \
                        else ch.raw[actual_start:stop].astype(np.float64)
                    color = _CHANNEL_COLORS[mux % len(_CHANNEL_COLORS)]
                    self._plot.plot(t, y, pen=pg.mkPen(color, width=1.5))
                    loaded += len(t)

            label = "Voltage" if self._display_mode == "voltage" else "Raw counts"
            units = "V" if self._display_mode == "voltage" else ""
            self._plot.setLabel("left", label, units=units)
            self._plot.setLabel("bottom", "Sample index")
            self._status_label.setText(
                f"{loaded:,} samples  ×  {len(chs)} channel(s)"
            )
            self._start_spin.blockSignals(True)
            self._start_spin.setValue(actual_start)
            self._start_spin.blockSignals(False)
        except Exception as e:
            self._status_label.setText(f"Load failed: {e}")

    # ── Internal: live ────────────────────────────────────────────────────────

    def _get_or_create_curve(self, mux: int) -> pg.PlotDataItem:
        if mux not in self._curves:
            color = _CHANNEL_COLORS[mux % len(_CHANNEL_COLORS)]
            curve = self._plot.plot(pen=pg.mkPen(color, width=1.5))
            self._curves[mux] = curve
        return self._curves[mux]

    def _refresh_live(self) -> None:
        if self._adc is None or self._plot_mode != "live":
            return
        win = self._live_win_spin.value()
        batch = self._adc.get_latest(win)
        if batch.n_samples == 0:
            return

        _rev = {v: k for k, v in MUX_LABELS.items()}

        n = batch.n_samples
        x = np.arange(n)
        for i, label in enumerate(batch.channel_labels):
            mux = _rev.get(label, i)
            curve = self._get_or_create_curve(mux)
            if mux not in self._visible_channels:
                curve.setVisible(False)
                continue
            curve.setVisible(True)
            y = batch.data[:, i]
            if self._display_mode != "voltage":
                y = y / LSB
            curve.setData(x, y)

        self._status_label.setText(
            f"{batch.n_samples:,} samples  ×  {batch.n_channels} channel(s)"
        )
