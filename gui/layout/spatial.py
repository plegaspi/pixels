from __future__ import annotations

import os

import numpy as np
import pyqtgraph as pg
import yaml
from PySide6.QtCore import QSettings, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from pixels.daq.adx134.data.conversion import PACKED_HEADER_WORDS
from pixels.daq.adx134.packet_store import PacketReader
from pixels.daq.adx134.profiles import RAW_PROFILE

LSB = 4.096 / (2**23)


class SpatialTab(QWidget):
    # Emitted when load_file() or start() switches the plot mode so the sidebar
    # radio can stay in sync without a circular signal loop.
    plot_mode_changed = Signal(str)  # "live" | "load"

    def __init__(self):
        super().__init__()
        self._mapping: list[tuple[int, int, int]] = []
        self._cell_to_ch: dict[tuple[int, int], int] = {}
        self._last_metric_values: np.ndarray | None = None
        self._grid_shape: tuple[int, int] = (1, 1)
        self._bin_paths: list[str] = []
        self._profile = RAW_PROFILE
        self._adx_data = None
        self._load_start: int | None = None
        self._plot_mode = "live"

        # Config state — driven by SpatialPlotCfgWidget in the sidebar
        self._metric: str = "Peak"
        self._window: int = 500
        self._colormap: str = "viridis"
        self._range_auto: bool = True
        self._vmin: float = 0.0
        self._vmax: float = 1.0

        # ── Plot area ─────────────────────────────────────────────────────────
        self._gfx = pg.GraphicsLayoutWidget()
        self._plot = self._gfx.addPlot(row=0, col=0)
        self._plot.setLabel("bottom", "Column")
        self._plot.setLabel("left", "Row")
        self._image = pg.ImageItem()
        self._plot.addItem(self._image)

        self._grid_overlay = pg.PlotCurveItem(pen=pg.mkPen("#ffffff", width=1))
        self._plot.addItem(self._grid_overlay)

        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.setMenuEnabled(False)
        self._plot.vb.disableAutoRange()  # prevent setImage from resetting the view

        # Apply LUT directly so ColorBarItem doesn't own the image's level mapping.
        self._cbar = None
        try:
            cm = pg.colormap.get("viridis")
            self._image.setLookupTable(cm.getLookupTable(nPts=256))
            self._cbar = pg.ColorBarItem(colorMap=cm, label="Magnitude")
            self._gfx.addItem(self._cbar, row=0, col=1)
        except Exception:
            pass

        # ── Load navigation ───────────────────────────────────────────────────
        self._load_nav = QWidget()
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(4, 2, 4, 2)
        nav_row = QHBoxLayout()
        self._btn_first = QPushButton("<<")
        self._btn_prev = QPushButton("<")
        self._nav_spin = QSpinBox()
        self._nav_spin.setRange(0, 1_000_000_000)
        self._nav_spin.setFixedWidth(90)
        self._btn_go = QPushButton("Go")
        self._btn_next = QPushButton(">")
        self._btn_last = QPushButton(">>")
        self._btn_latest = QPushButton("Latest")
        for w in (
            self._btn_first,
            self._btn_prev,
            self._nav_spin,
            self._btn_go,
            self._btn_next,
            self._btn_last,
            self._btn_latest,
        ):
            nav_row.addWidget(w)
        nav_layout.addLayout(nav_row)
        self._lbl_nav = QLabel("")
        self._lbl_nav.setStyleSheet("color: #888; font-size: 11px;")
        nav_layout.addWidget(self._lbl_nav)
        self._load_nav.setLayout(nav_layout)
        self._load_nav.setVisible(False)

        self._status = QLabel("No data")
        self._status.setStyleSheet("color: #888;")

        # ── Layout ────────────────────────────────────────────────────────────
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._load_nav)
        layout.addWidget(self._gfx, 1)
        layout.addWidget(self._status)
        self.setLayout(layout)

        # ── Timer ─────────────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._live_refresh)

        # ── Nav signals ───────────────────────────────────────────────────────
        self._btn_first.clicked.connect(self._go_first)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_go.clicked.connect(self._go_to_spin)
        self._nav_spin.editingFinished.connect(self._go_to_spin)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_last.clicked.connect(self._go_last)
        self._btn_latest.clicked.connect(self._go_latest)

        # ── Hover tooltip ─────────────────────────────────────────────────────
        self._hover_proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved,
            rateLimit=30,
            slot=self._on_mouse_moved,
        )

        # Restore last map from previous session
        last = QSettings().value("spatial/last_map_path", "")
        if last and os.path.exists(last):
            self.load_map(last)

    # ── Public setters (driven by SpatialPlotCfgWidget signals) ──────────────

    def set_plot_mode(self, mode: str) -> None:
        """Called by sidebar radio button — switches live/load without emitting plot_mode_changed."""
        self._switch_mode(mode, emit=False)
        if mode == "load":
            self._load_refresh()

    def set_metric(self, metric: str) -> None:
        self._metric = metric
        self._refresh()

    def set_window(self, n: int) -> None:
        self._window = n
        self._refresh()

    def set_colormap(self, name: str) -> None:
        self._colormap = name
        try:
            cm = pg.colormap.get(name)
            self._image.setLookupTable(cm.getLookupTable(nPts=256))
            if self._cbar is not None:
                self._cbar.setColorMap(cm)
        except Exception:
            pass
        self._refresh()

    def set_range(self, auto: bool, vmin: float, vmax: float) -> None:
        self._range_auto = auto
        self._vmin = vmin
        self._vmax = vmax
        self._refresh()

    # ── Public interface ──────────────────────────────────────────────────────

    def load_map(self, path: str) -> None:
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f)
            grid = cfg.get("grid", {})
            rows = int(grid.get("rows", 1))
            cols = int(grid.get("cols", 1))
            mapping: list[tuple[int, int, int]] = []
            channels = cfg.get("channels") or {}
            if isinstance(channels, dict):
                for k, v in channels.items():
                    mapping.append((int(k), int(v[0]), int(v[1])))
            else:
                for entry in channels:
                    if isinstance(entry, dict):
                        mapping.append(
                            (
                                int(entry["channel"]),
                                int(entry["row"]),
                                int(entry["col"]),
                            )
                        )
                    else:
                        ch_idx, row, col = entry
                        mapping.append((int(ch_idx), int(row), int(col)))
            self._grid_shape = (rows, cols)
            self._mapping = mapping
            self._cell_to_ch = {(r, c): ch for ch, r, c in mapping}
            self._set_image_rect()
            self._refresh()
        except Exception as e:
            self._status.setText(f"Map load failed: {e}")

    def start(self, daq) -> None:
        self._bin_paths = [daq.capture_path(i) for i in range(daq.n_ports)]
        self._profile = daq.profile
        if self._adx_data is not None:
            try:
                self._adx_data.close()
            except Exception:
                pass
            self._adx_data = None
        self._switch_mode("live", emit=True)

    def stop(self) -> None:
        self._timer.stop()

    def load_file(self, bins_dir: str) -> None:
        from pathlib import Path
        from pixels.models import ADX134Data

        if self._adx_data is not None:
            try:
                self._adx_data.close()
            except Exception:
                pass
            self._adx_data = None
        try:
            self._adx_data = ADX134Data(Path(bins_dir))
        except Exception as e:
            self._status.setText(f"Load failed: {e}")
            return
        self._load_start = None
        self._switch_mode("load", emit=True)
        self._load_refresh()

    # ── Internal: mode ────────────────────────────────────────────────────────

    def _switch_mode(self, mode: str, *, emit: bool) -> None:
        self._plot_mode = mode
        self._load_nav.setVisible(mode == "load")
        if mode == "live":
            if self._bin_paths:
                self._timer.start()
        else:
            self._timer.stop()
        if emit:
            self.plot_mode_changed.emit(mode)

    def _set_image_rect(self) -> None:
        rows, cols = self._grid_shape
        # setRect deferred to _render_grid (needs image data to compute transform).
        # Set the view range now so the axes look right before the first render.
        self._plot.setXRange(-0.5, cols - 0.5, padding=0)
        self._plot.setYRange(-0.5, rows - 0.5, padding=0)
        self._draw_grid(rows, cols)

    def _draw_grid(self, rows: int, cols: int) -> None:
        x: list[float] = []
        y: list[float] = []
        for col in range(cols + 1):
            cx = col - 0.5
            x += [cx, cx]
            y += [-0.5, rows - 0.5]
        for row in range(rows + 1):
            ry = row - 0.5
            x += [-0.5, cols - 0.5]
            y += [ry, ry]
        self._grid_overlay.setData(
            np.array(x, dtype=np.float64),
            np.array(y, dtype=np.float64),
            connect="pairs",
        )

    # ── Internal: compute + render ────────────────────────────────────────────

    def _compute_metric(self, data: np.ndarray) -> np.ndarray:
        if self._metric == "RMS":
            return np.sqrt(np.mean(data**2, axis=0))
        elif self._metric == "Peak":
            return np.max(np.abs(data), axis=0)
        else:  # Mean
            return np.mean(np.abs(data), axis=0)

    def _render_grid(self, metric_values: np.ndarray, label: str = "") -> None:
        self._last_metric_values = metric_values
        rows, cols = self._grid_shape
        grid = np.full((rows, cols), np.nan, dtype=np.float32)
        for ch_idx, r, c in self._mapping:
            if r < rows and c < cols and ch_idx < len(metric_values):
                grid[r, c] = float(metric_values[ch_idx])

        occupied = grid[~np.isnan(grid)]
        if len(occupied) == 0:
            self._status.setText("No mapped channels in data")
            return

        if self._range_auto:
            vmin, vmax = float(occupied.min()), float(occupied.max())
            if vmin == vmax:
                vmax = vmin + 1e-12
        else:
            vmin, vmax = self._vmin, self._vmax
            if vmin >= vmax:
                vmax = vmin + 1e-12

        display = np.where(np.isnan(grid), vmin, grid)
        self._image.setImage(display.T, levels=(vmin, vmax))
        # setRect must be called AFTER setImage — pyqtgraph computes the
        # pixel->data transform from the image's shape, so calling it before
        # setImage (when image is None) produces an identity/wrong transform.
        self._image.setRect(-0.5, -0.5, cols, rows)
        self._plot.setXRange(-0.5, cols - 0.5, padding=0)
        self._plot.setYRange(-0.5, rows - 0.5, padding=0)
        if self._cbar is not None:
            try:
                self._cbar.setLevels((vmin, vmax))
            except Exception:
                pass
        n_valid = int(np.sum(~np.isnan(grid)))
        self._status.setText(
            f"{self._metric}  {vmin:.4g} – {vmax:.4g}  |  {n_valid}/{rows * cols} cells  |  {label}"
        )

    def _refresh(self) -> None:
        if self._plot_mode == "live":
            self._live_refresh()
        else:
            self._load_refresh()

    # ── Live refresh ──────────────────────────────────────────────────────────

    def _live_refresh(self) -> None:
        if not self._bin_paths or not self._mapping:
            return

        spf = self._profile.samples_per_frame
        cpp = self._profile.n_chan_per_port
        win_pkts = max(1, self._window // spf)

        raw_ports: list[np.ndarray] = []
        for path in self._bin_paths:
            if not os.path.exists(path):
                return
            try:
                with PacketReader(
                    path, swmr=path.endswith((".h5", ".hdf5")), profile=self._profile
                ) as reader:
                    reader.refresh()
                    n_pkts = reader.n_packets
                    self._profile = reader.profile
                    spf = self._profile.samples_per_frame
                    cpp = self._profile.n_chan_per_port
                    start = max(0, n_pkts - win_pkts)
                    if n_pkts - start < 1:
                        return
                    words = reader.read_words(start, n_pkts)
            except (OSError, KeyError, ValueError):
                return

            n = n_pkts - start
            packed_bytes = spf * cpp * 3
            payload = words[:, PACKED_HEADER_WORDS:].astype(">u4", copy=False)
            payload_bytes = payload.view(np.uint8).reshape(n, packed_bytes)
            byte_codes = payload_bytes.reshape(n, spf, cpp, 3).astype(np.int32)
            unsigned = (
                (byte_codes[:, :, :, 0] << 16)
                | (byte_codes[:, :, :, 1] << 8)
                | byte_codes[:, :, :, 2]
            )
            codes = unsigned.astype(np.int32)
            codes[codes >= 0x800000] -= 0x1000000
            raw_ports.append(codes.reshape(-1, cpp) * LSB)

        if not raw_ports:
            return

        data = np.concatenate(raw_ports, axis=1)
        metric_values = self._compute_metric(data)
        ch_info = (
            f"{data.shape[1]} ch  metric[{metric_values.min():.3g}"
            f"–{metric_values.max():.3g}]"
        )
        self._render_grid(metric_values, f"Live — {data.shape[0]:,} smp  {ch_info}")

    # ── Load refresh ──────────────────────────────────────────────────────────

    def _load_refresh(self) -> None:
        if self._adx_data is None or not self._mapping:
            return

        d = self._adx_data
        n_total = d.n_samples
        n = self._window
        self._lbl_nav.setText(f"{n_total:,} samples total")

        actual_start = (
            max(0, n_total - n)
            if self._load_start is None
            else max(0, min(self._load_start, n_total - 1))
        )
        stop = min(actual_start + n, n_total)

        try:
            data = d.read_voltage(actual_start, stop)
        except Exception as e:
            self._status.setText(f"Read failed: {e}")
            return

        metric_values = self._compute_metric(data)
        ch_info = (
            f"{data.shape[1]} ch  metric[{metric_values.min():.3g}"
            f"–{metric_values.max():.3g}]"
        )
        self._render_grid(
            metric_values, f"[{actual_start:,} – {stop:,}] / {n_total:,} smp  {ch_info}"
        )
        self._nav_spin.blockSignals(True)
        self._nav_spin.setValue(actual_start)
        self._nav_spin.blockSignals(False)

    # ── Hover tooltip ─────────────────────────────────────────────────────────

    def _on_mouse_moved(self, evt) -> None:
        pos = evt[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            QToolTip.hideText()
            return
        mp = self._plot.vb.mapSceneToView(pos)
        col = int(round(mp.x()))
        row = int(round(mp.y()))
        rows, cols = self._grid_shape
        if not (0 <= row < rows and 0 <= col < cols):
            QToolTip.hideText()
            return
        ch = self._cell_to_ch.get((row, col))
        mv = self._last_metric_values
        if ch is not None and mv is not None and ch < len(mv):
            QToolTip.showText(
                QCursor.pos(),
                f"ch{ch}  ({row}, {col})\n{self._metric}: {mv[ch]:.4g} V",
            )
        else:
            QToolTip.hideText()

    # ── Load navigation ───────────────────────────────────────────────────────

    def _total_samples(self) -> int:
        return self._adx_data.n_samples if self._adx_data else 0

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
        n = self._window
        if self._load_start is None:
            self._load_start = max(0, self._total_samples() - n)
        self._load_start = max(0, self._load_start - n)
        self._load_refresh()

    def _go_next(self) -> None:
        if self._load_start is None:
            return
        n = self._window
        total = self._total_samples()
        nxt = self._load_start + n
        self._load_start = None if nxt >= total - n else nxt
        self._load_refresh()

    def _go_to_spin(self) -> None:
        self._load_start = self._nav_spin.value()
        self._load_refresh()
