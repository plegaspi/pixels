"""Device-agnostic live plot widget driven by DAQDevice.get_latest()."""

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

from pixels.daq._base import DAQDevice

_DEFAULT_WINDOW = 5_000
_CHANNEL_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
]


class LivePlotWidget(QWidget):
    """
    Live plot driven by any DAQDevice via get_latest() -> SampleBatch.

    Usage:
        widget = LivePlotWidget()
        widget.start(device)   # begins 100ms polling loop
        widget.stop()          # detaches device and stops timer
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device: DAQDevice | None = None
        self._window: int = _DEFAULT_WINDOW
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._visible_labels: set[str] | None = None  # None = show all

        pg.setConfigOption("background", "w")
        pg.setConfigOption("foreground", "k")

        self._plot = pg.PlotWidget()
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Amplitude")

        self._win_spin = QSpinBox()
        self._win_spin.setRange(10, 1_000_000)
        self._win_spin.setValue(_DEFAULT_WINDOW)
        self._win_spin.setFixedWidth(130)
        self._win_spin.setSuffix(" samples")
        self._win_spin.valueChanged.connect(self.set_window)

        self._autoscale_btn = QPushButton("Autoscale")
        self._autoscale_btn.clicked.connect(self._plot.enableAutoRange)

        self._status_label = QLabel("No device")
        self._status_label.setStyleSheet("color: #888;")

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Window:"))
        ctrl.addWidget(self._win_spin)
        ctrl.addWidget(self._autoscale_btn)
        ctrl.addStretch()
        ctrl.addWidget(self._status_label)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(ctrl)
        layout.addWidget(self._plot, stretch=1)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, device: DAQDevice, window: int | None = None) -> None:
        """Attach a device and begin live plotting."""
        self._device = device
        if window is not None:
            self.set_window(window)
        self._curves.clear()
        self._plot.clear()
        self._plot.setLabel("left", "Amplitude")
        self._status_label.setText("Waiting for data…")
        self._timer.start()

    def stop(self) -> None:
        """Detach device and stop the timer."""
        self._timer.stop()
        self._device = None
        self._status_label.setText("Stopped")

    def set_window(self, n: int) -> None:
        self._window = n
        self._win_spin.blockSignals(True)
        self._win_spin.setValue(n)
        self._win_spin.blockSignals(False)

    def set_visible_channels(self, labels: list[str] | None) -> None:
        """Pass None to show all channels, or a list of label strings to filter."""
        self._visible_labels = set(labels) if labels is not None else None
        for label, curve in self._curves.items():
            visible = self._visible_labels is None or label in self._visible_labels
            curve.setVisible(visible)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_or_create_curve(self, label: str, index: int) -> pg.PlotDataItem:
        if label not in self._curves:
            color = _CHANNEL_COLORS[index % len(_CHANNEL_COLORS)]
            curve = self._plot.plot(pen=pg.mkPen(color, width=1.5), name=label)
            self._curves[label] = curve
        return self._curves[label]

    def _refresh(self) -> None:
        if self._device is None:
            return
        try:
            batch = self._device.get_latest(self._window)
        except Exception:
            return

        if batch.n_samples == 0:
            return

        self._plot.setLabel("left", "Amplitude", units=batch.units)

        active_labels = set(batch.channel_labels)

        # Hide curves for channels no longer present
        for label, curve in self._curves.items():
            if label not in active_labels:
                curve.setVisible(False)

        for i, label in enumerate(batch.channel_labels):
            curve = self._get_or_create_curve(label, i)
            visible = self._visible_labels is None or label in self._visible_labels
            if not visible:
                curve.setVisible(False)
                continue
            curve.setVisible(True)
            curve.setData(batch.time, batch.data[:, i])

        self._status_label.setText(
            f"{batch.n_samples:,} samples  ×  {batch.n_channels} ch  "
            f"@ {batch.sample_rate:,.0f} Hz"
        )
