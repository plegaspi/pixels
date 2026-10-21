import os

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixels.daq.adx134.packet_store import PacketReader
from pixels.daq.adx134.data.conversion import PACKED_HEADER_WORDS
from pixels.daq.adx134.profiles import RAW_PROFILE

LSB = 4.096 / (2**23)
N_SAMPLES_DEFAULT = 20_000
_DEFAULT_LIVE_WINDOW = 5_000
_DEFAULT_LAG = 3
_PLL_F_OUT = 5_000.0  # Hz — FPGA demod output rate (500 kHz / DECIM_RATIO=100)
_PLL_CUTOFF_DEFAULT = 1_000.0


def _track_and_recover(
    I: np.ndarray,
    Q: np.ndarray,
    pll_cutoff: float = _PLL_CUTOFF_DEFAULT,
    phi_init: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """IIR carrier-phase tracker. I, Q: (n_samples, n_channels). Returns (recovered, phi_final)."""
    alpha = 1 - np.exp(-2 * np.pi * pll_cutoff / _PLL_F_OUT)
    n, c = I.shape
    raw_phi = np.arctan2(I, Q)
    phi_tracked = np.zeros((n, c))
    phi_prev = phi_init if phi_init is not None else raw_phi[0].copy()
    for k in range(n):
        delta = raw_phi[k] - phi_prev
        delta = (delta + np.pi) % (2 * np.pi) - np.pi
        phi_prev = alpha * (phi_prev + delta) + (1 - alpha) * phi_prev
        phi_tracked[k] = phi_prev
    return I * np.sin(phi_tracked) + Q * np.cos(phi_tracked), phi_prev


# ── Display mode constants ────────────────────────────────────────────────────

_RAW_DISPLAY_ITEMS = ["Voltage", "ADC Codes", "Voltage + ADC Codes"]
_DEMOD_DISPLAY_ITEMS = [
    "I/Q Signal",
    "Amplitude",
    "Phase",
    "Amplitude + Phase",
    "I/Q + Amplitude",
    "I/Q + Phase",
]
_DISP_MODE_MAP = {
    "Voltage": "voltage",
    "ADC Codes": "codes",
    "Voltage + ADC Codes": "both",
    "I/Q Signal": "iq",
    "Amplitude": "amplitude",
    "Phase": "phase",
    "Amplitude + Phase": "amplitude_phase",
    "I/Q + Amplitude": "iq_amplitude",
    "I/Q + Phase": "iq_phase",
}


# ── Combined data tab ─────────────────────────────────────────────────────────


class DataTab(QWidget):
    """Single tab that handles both HDF5 file browsing and live capture display."""

    def __init__(self):
        super().__init__()
        self._adx_data = None  # ADX134Data instance for file-load mode
        self._start: int | None = None
        self._bin_paths: list[str] = []
        self._profile = RAW_PROFILE
        self._cpp = self._profile.n_chan_per_port
        self._curves_v: list[pg.PlotDataItem] = []
        self._curves_u: list[pg.PlotDataItem] = []
        self._hidden_ports: set[int] = set()
        self._channel_rows: list = []
        self._demod_mode: bool = False
        self._display_mode: str = "voltage"
        self._curves_v_raw_ch: list[int] = []
        self._curves_u_raw_ch: list[int] = []
        self._carrier_vpp: float = 0.0

        pg.setConfigOption("background", "w")
        pg.setConfigOption("foreground", "k")

        # ── Shared demod controls ─────────────────────────────────────────────
        self._pll_cutoff_spin = QDoubleSpinBox()
        self._pll_cutoff_spin.setRange(1.0, 2500.0)
        self._pll_cutoff_spin.setValue(_PLL_CUTOFF_DEFAULT)
        self._pll_cutoff_spin.setDecimals(0)
        self._pll_cutoff_spin.setSuffix(" Hz")
        self._pll_cutoff_spin.setFixedWidth(100)
        self._pll_cutoff_spin.setToolTip(
            "PLL cutoff frequency for recovered signal derotation"
        )

        # ── Load-data controls ────────────────────────────────────────────────
        self._btn_first = QPushButton("<<")
        self._btn_prev = QPushButton("<")
        self._start_spin = QSpinBox()
        self._start_spin.setRange(0, 100_000_000)
        self._start_spin.setFixedWidth(120)
        self._btn_go = QPushButton("Go")
        self._btn_next = QPushButton(">")
        self._btn_last = QPushButton(">>")
        self._btn_latest = QPushButton("Latest")
        self._win_spin = QSpinBox()
        self._win_spin.setRange(100, 1_000_000)
        self._win_spin.setValue(N_SAMPLES_DEFAULT)
        self._win_spin.setFixedWidth(130)
        self._btn_autoscale = QPushButton("Autoscale")
        self._lbl_total = QLabel("")
        self._lbl_total.setStyleSheet("color: #888;")

        load_nav = QHBoxLayout()
        for w in (self._btn_first, self._btn_prev):
            load_nav.addWidget(w)
        load_nav.addWidget(QLabel("Start index:"))
        load_nav.addWidget(self._start_spin)
        for w in (self._btn_go, self._btn_next, self._btn_last, self._btn_latest):
            load_nav.addWidget(w)
        load_nav.addWidget(QLabel("  Window:"))
        load_nav.addWidget(self._win_spin)
        load_nav.addWidget(self._btn_autoscale)
        load_nav.addStretch()
        load_nav.addWidget(self._lbl_total)

        self._load_controls = QWidget()
        self._load_controls.setLayout(load_nav)

        # ── Live controls ─────────────────────────────────────────────────────
        self._live_win_spin = QSpinBox()
        self._live_win_spin.setRange(100, 500_000)
        self._live_win_spin.setValue(_DEFAULT_LIVE_WINDOW)
        self._live_win_spin.setFixedWidth(130)
        self._live_win_spin.setSuffix(" samples")

        self._lag_spin = QSpinBox()
        self._lag_spin.setRange(1, 200)
        self._lag_spin.setValue(_DEFAULT_LAG)
        self._lag_spin.setFixedWidth(90)
        self._lag_spin.setSuffix(" pkt lag")

        self._lbl_live_status = QLabel("Idle")
        self._lbl_live_status.setStyleSheet("color: #888;")

        live_ctrl = QHBoxLayout()
        live_ctrl.addWidget(QLabel("Window:"))
        live_ctrl.addWidget(self._live_win_spin)
        live_ctrl.addWidget(QLabel("  Lag:"))
        live_ctrl.addWidget(self._lag_spin)
        live_ctrl.addWidget(self._btn_autoscale)
        live_ctrl.addStretch()
        live_ctrl.addWidget(self._lbl_live_status)

        self._live_controls = QWidget()
        self._live_controls.setLayout(live_ctrl)
        self._live_controls.setVisible(False)

        # ── Shared plots ──────────────────────────────────────────────────────
        self._plot_v = pg.PlotWidget(title="Voltage")
        self._plot_v.setLabel("left", "Voltage", units="V")
        self._plot_v.setLabel("bottom", "Sample index")
        self._plot_v.showGrid(x=True, y=True, alpha=0.3)

        self._plot_u = pg.PlotWidget(title="ADC Counts")
        self._plot_u.setLabel("left", "ADC Counts")
        self._plot_u.setLabel("bottom", "Sample index")
        self._plot_u.showGrid(x=True, y=True, alpha=0.3)
        self._plot_u.setXLink(self._plot_v)
        self._plot_u.setVisible(False)  # default display mode is "Voltage" only

        layout = QVBoxLayout()
        layout.addWidget(self._load_controls)
        layout.addWidget(self._live_controls)
        layout.addWidget(self._plot_v, stretch=1)
        layout.addWidget(self._plot_u, stretch=1)
        self.setLayout(layout)

        # ── Wire load-data buttons ────────────────────────────────────────────
        self._btn_first.clicked.connect(self._go_first)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_go.clicked.connect(self._go_to_spin)
        self._start_spin.editingFinished.connect(self._go_to_spin)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_last.clicked.connect(self._go_last)
        self._btn_latest.clicked.connect(self._go_latest)
        self._win_spin.editingFinished.connect(self._load_refresh)
        self._pll_cutoff_spin.editingFinished.connect(self._load_refresh)
        self._btn_autoscale.clicked.connect(self._autoscale)

        # ── Live timer ────────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._live_refresh)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_demod_mode(self, enabled: bool) -> None:
        # Loaded file data's demod_mode is authoritative; don't let a capture-config
        # change (which fires this signal) override it while a file is still open.
        if self._adx_data is not None:
            return
        self._demod_mode = enabled
        self._curves_v.clear()
        self._curves_u.clear()
        self._curves_v_raw_ch.clear()
        self._curves_u_raw_ch.clear()
        self._plot_v.clear()
        self._plot_u.clear()

    def set_display_mode(self, mode: str) -> None:
        self._display_mode = mode
        v_modes = (
            "both",
            "voltage",
            "iq",
            "amplitude",
            "phase",
            "amplitude_phase",
            "iq_amplitude",
            "iq_phase",
        )
        u_modes = ("both", "codes", "amplitude_phase", "iq_amplitude", "iq_phase")
        self._plot_v.setVisible(mode in v_modes)
        self._plot_u.setVisible(mode in u_modes)
        self._update_plot_labels()
        self._curves_v.clear()
        self._curves_u.clear()
        self._curves_v_raw_ch.clear()
        self._curves_u_raw_ch.clear()
        self._plot_v.clear()
        self._plot_u.clear()
        if self._adx_data is not None:
            self._load_refresh()

    def set_carrier_vpp(self, vpp: float) -> None:
        self._carrier_vpp = vpp
        if self._adx_data is not None:
            self._load_refresh()

    def _normalize(self, data: np.ndarray) -> np.ndarray:
        """Scale amplitude/recovered data to physical carrier envelope volts."""
        if self._carrier_vpp <= 0 or self._display_mode not in (
            "amplitude",
            "recovered",
        ):
            return data
        mean = data.mean(axis=0, keepdims=True)
        mean = np.where(np.abs(mean) < 1e-12, 1.0, mean)
        return data / mean * (self._carrier_vpp / 2.0)

    def _update_plot_labels(self, live: bool = False) -> None:
        suffix = " (live)" if live else ""
        bottom = "Sample offset" if live else "Sample index"
        m = self._display_mode
        if m == "iq":
            self._plot_v.setTitle(f"I/Q Signal{suffix}")
            self._plot_v.setLabel("left", "Value", units="V")
        elif m == "amplitude":
            self._plot_v.setTitle(f"Amplitude{suffix}")
            self._plot_v.setLabel("left", "Amplitude", units="V")
        elif m == "phase":
            self._plot_v.setTitle(f"Phase{suffix}")
            self._plot_v.setLabel("left", "Phase", units="rad")
        elif m == "recovered":
            self._plot_v.setTitle(f"Recovered Signal{suffix}")
            self._plot_v.setLabel("left", "Signal", units="V")
        elif m == "amplitude_phase":
            self._plot_v.setTitle(f"Amplitude{suffix}")
            self._plot_u.setTitle(f"Phase{suffix}")
            self._plot_v.setLabel("left", "Amplitude", units="V")
            self._plot_u.setLabel("left", "Phase", units="rad")
        elif m == "iq_amplitude":
            self._plot_v.setTitle(f"I/Q Signal{suffix}")
            self._plot_u.setTitle(f"Amplitude{suffix}")
            self._plot_v.setLabel("left", "Value", units="V")
            self._plot_u.setLabel("left", "Amplitude", units="V")
        elif m == "iq_phase":
            self._plot_v.setTitle(f"I/Q Signal{suffix}")
            self._plot_u.setTitle(f"Phase{suffix}")
            self._plot_v.setLabel("left", "Value", units="V")
            self._plot_u.setLabel("left", "Phase", units="rad")
        elif m == "codes":
            self._plot_u.setTitle(f"ADC Counts{suffix}")
            self._plot_u.setLabel("left", "ADC Counts")
        else:  # voltage / both
            self._plot_v.setTitle(f"Voltage{suffix}")
            self._plot_u.setTitle(f"ADC Counts{suffix}")
            self._plot_v.setLabel("left", "Voltage", units="V")
            self._plot_u.setLabel("left", "ADC Counts")
        self._plot_v.setLabel("bottom", bottom)
        self._plot_u.setLabel("bottom", bottom)

    def set_live_mode(self, live: bool) -> None:
        """Switch visible controls between live and load-data. Does not touch the timer."""
        self._load_controls.setVisible(not live)
        self._live_controls.setVisible(live)
        self._update_plot_labels(live=live)

    def load_file(self, bins_dir: str, channel_rows=None) -> None:
        from pixels.models import ADX134Data
        from pathlib import Path

        if self._adx_data is not None:
            self._adx_data.close()
        self._adx_data = ADX134Data(Path(bins_dir))
        self._profile = self._adx_data.profile
        self._channel_rows = channel_rows or []
        self._start = None
        self._cpp = self._profile.n_chan_per_port
        self._demod_mode = self._profile.demod_mode
        self._hidden_ports.clear()
        self._curves_v.clear()
        self._curves_u.clear()
        self._plot_v.clear()
        self._plot_u.clear()
        self._load_refresh()

    def update_channels(self, channel_rows) -> None:
        self._channel_rows = channel_rows
        self._apply_visibility()

    def toggle_port(self, port_idx: int, checked: bool) -> None:
        if checked:
            self._hidden_ports.discard(port_idx)
        else:
            self._hidden_ports.add(port_idx)
        self._apply_visibility()

    def show_all_ports(self) -> None:
        self._hidden_ports.clear()
        self._apply_visibility()

    def start(self, daq, mode: str = "live") -> None:
        # Clear stale file data so set_display_mode doesn't call _load_refresh
        # with a mismatched _demod_mode during live capture.
        if self._adx_data is not None:
            self._adx_data.close()
            self._adx_data = None
        self._bin_paths = [daq.capture_path(i) for i in range(daq.n_ports)]
        self._profile = daq.profile
        self._cpp = self._profile.n_chan_per_port
        self._demod_mode = self._profile.demod_mode
        self._curves_v.clear()
        self._curves_u.clear()
        self._curves_v_raw_ch.clear()
        self._curves_u_raw_ch.clear()
        self._plot_v.clear()
        self._plot_u.clear()
        self.set_plot_mode(mode)

    def set_plot_mode(self, mode: str) -> None:
        """Switch between "live", "load", and "disabled" at any time."""
        self._timer.stop()
        if mode == "live":
            self._plot_v.clear()
            self._plot_u.clear()
            self._curves_v.clear()
            self._curves_u.clear()
            self._plot_v.enableAutoRange()
            self._plot_u.enableAutoRange()
            self.set_live_mode(True)
            if self._bin_paths:
                self._lbl_live_status.setStyleSheet("color: green;")
                # self._lbl_live_status.setText("Live")
                self._timer.start()
        elif mode == "load":
            self.set_live_mode(False)
        else:  # disabled
            self._live_controls.setVisible(False)
            self._load_controls.setVisible(False)
            self._plot_v.clear()
            self._plot_u.clear()
            self._curves_v.clear()
            self._curves_u.clear()
            self._curves_v_raw_ch.clear()
            self._curves_u_raw_ch.clear()
            self._plot_v.setTitle("Voltage")
            self._plot_u.setTitle("ADC Counts")
            self._plot_v.setLabel("bottom", "Sample index")
            self._plot_u.setLabel("bottom", "Sample index")

    def stop(self) -> None:
        self._timer.stop()
        self._lbl_live_status.setStyleSheet("color: #888;")
        self._lbl_live_status.setText("Stopped")
        if self._adx_data is not None:
            self._adx_data.close()
            self._adx_data = None

    # ── Private: shared ───────────────────────────────────────────────────────

    def _pen_for(self, ch: int, n_ch: int) -> object:
        if ch < len(self._channel_rows):
            return pg.mkPen(color=self._channel_rows[ch].color.name(), width=1)
        return pg.mkPen(color=pg.intColor(ch, n_ch), width=1)

    def _demod_raw_ch_maps(
        self, n_v: int, n_u: int, mode: str
    ) -> tuple[list[int], list[int]]:
        """Map demod curve index -> raw ADC channel index for visibility/colour lookup.

        Raw channel index is port * n_real + slot (same numbering as raw mode), so a
        CHxxx row refers to the same physical channel in every display mode.
        """
        cpp = self._cpp
        n_real = cpp // 2

        def iq_map(n: int) -> list[int]:
            # I/Q columns per port: [I0..I(n_real-1), Q0..Q(n_real-1)]
            return [(i // cpp) * n_real + (i % cpp) % n_real for i in range(n)]

        def amp_map(n: int) -> list[int]:
            # Amplitude/phase columns are already ordered port * n_real + slot
            return list(range(n))

        if mode == "iq":
            return iq_map(n_v), []
        elif mode in ("amplitude", "phase", "recovered"):
            return amp_map(n_v), []
        elif mode == "amplitude_phase":
            m = amp_map(n_v)
            return m, m[:]
        elif mode in ("iq_amplitude", "iq_phase"):
            return iq_map(n_v), amp_map(n_u)
        else:  # raw modes: voltage / both / codes
            return list(range(n_v)), list(range(n_u))

    def _apply_visibility(self) -> None:
        n_total = max(len(self._curves_v) + len(self._curves_u), 1)

        def _apply_one(curve: pg.PlotDataItem, raw_ch: int) -> None:
            if raw_ch < len(self._channel_rows):
                row = self._channel_rows[raw_ch]
                pen = pg.mkPen(color=row.color.name(), width=1)
                visible = row.enabled
            else:
                pen = pg.mkPen(color=pg.intColor(raw_ch, n_total), width=1)
                visible = True
            curve.setPen(pen)
            curve.setVisible(visible)

        for i, cv in enumerate(self._curves_v):
            raw_ch = self._curves_v_raw_ch[i] if i < len(self._curves_v_raw_ch) else i
            _apply_one(cv, raw_ch)

        for i, cu in enumerate(self._curves_u):
            raw_ch = self._curves_u_raw_ch[i] if i < len(self._curves_u_raw_ch) else i
            _apply_one(cu, raw_ch)

    def _autoscale(self) -> None:
        self._plot_v.enableAutoRange()
        self._plot_u.enableAutoRange()

    # ── Private: load-data ────────────────────────────────────────────────────

    def _go_first(self):
        self._start = 0
        self._load_refresh()

    def _go_last(self):
        self._start = None
        self._load_refresh()

    def _go_latest(self):
        self._start = None
        self._load_refresh()

    def _go_prev(self):
        if self._adx_data is None:
            return
        n = self._win_spin.value()
        if self._start is None:
            self._start = max(0, self._adx_data.n_samples - n)
        self._start = max(0, self._start - n)
        self._load_refresh()

    def _go_next(self):
        if self._start is None or self._adx_data is None:
            return
        n = self._win_spin.value()
        total = self._adx_data.n_samples
        nxt = self._start + n
        self._start = None if nxt >= total - n else nxt
        self._load_refresh()

    def _go_to_spin(self):
        self._start = self._start_spin.value()
        self._load_refresh()

    def _load_refresh(self):
        if self._adx_data is None:
            return

        d = self._adx_data
        n = self._win_spin.value()
        total = d.n_samples
        self._lbl_total.setText(f"{total:,} samples total")
        if total == 0:
            return

        actual_start = (
            max(0, total - n)
            if self._start is None
            else max(0, min(self._start, total - 1))
        )
        stop = min(actual_start + n, total)

        if self._demod_mode:
            mode = self._display_mode
            pll_cutoff = self._pll_cutoff_spin.value()
            if mode == "iq":
                primary = d.read_voltage(actual_start, stop)
                secondary = None
            elif mode == "amplitude":
                primary = d.read_amplitude(actual_start, stop)
                secondary = None
            elif mode == "phase":
                primary = d.read_phase(actual_start, stop)
                secondary = None
            elif mode == "recovered":
                from pixels.daq.adx134.data.constants import LSB as _LSB

                raw = d.read_samples(actual_start, stop).astype(np.float64) * _LSB
                I, Q = d._split_iq(raw)
                primary, _ = _track_and_recover(I, Q, pll_cutoff)
                secondary = None
            elif mode == "amplitude_phase":
                primary = d.read_amplitude(actual_start, stop)
                secondary = d.read_phase(actual_start, stop)
            elif mode == "iq_amplitude":
                primary = d.read_voltage(actual_start, stop)
                secondary = d.read_amplitude(actual_start, stop)
            else:  # iq_phase
                primary = d.read_voltage(actual_start, stop)
                secondary = d.read_phase(actual_start, stop)

            primary = self._normalize(primary)
            x = np.arange(actual_start, actual_start + primary.shape[0])
            n_v = primary.shape[1]
            n_u = secondary.shape[1] if secondary is not None else 0
            total_curves = n_v + n_u
            if total_curves != len(self._curves_v) + len(self._curves_u):
                self._plot_v.clear()
                self._plot_u.clear()
                v_map, u_map = self._demod_raw_ch_maps(n_v, n_u, mode)
                self._curves_v_raw_ch = v_map
                self._curves_u_raw_ch = u_map
                self._curves_v = [
                    self._plot_v.plot(
                        pen=self._pen_for(v_map[ch] if ch < len(v_map) else ch, n_v)
                    )
                    for ch in range(n_v)
                ]
                self._curves_u = [
                    self._plot_u.plot(
                        pen=self._pen_for(u_map[ch] if ch < len(u_map) else ch, n_u)
                    )
                    for ch in range(n_u)
                ]
            for ch, cv in enumerate(self._curves_v):
                cv.setData(x, primary[:, ch])
            if secondary is not None:
                for ch, cu in enumerate(self._curves_u):
                    cu.setData(x, secondary[:, ch])
        else:
            voltage = d.read_voltage(actual_start, stop)
            unsigned = d.read_samples(actual_start, stop).astype(np.uint32) & 0x00FFFFFF
            x = np.arange(actual_start, actual_start + voltage.shape[0])
            n_ch = voltage.shape[1]
            if n_ch != len(self._curves_v):
                self._plot_v.clear()
                self._plot_u.clear()
                self._curves_v_raw_ch = list(range(n_ch))
                self._curves_u_raw_ch = list(range(n_ch))
                self._curves_v = [
                    self._plot_v.plot(pen=self._pen_for(ch, n_ch)) for ch in range(n_ch)
                ]
                self._curves_u = [
                    self._plot_u.plot(pen=self._pen_for(ch, n_ch)) for ch in range(n_ch)
                ]
            for ch, (cv, cu) in enumerate(zip(self._curves_v, self._curves_u)):
                cv.setData(x, voltage[:, ch])
                cu.setData(x, unsigned[:, ch])

        self._apply_visibility()
        self._plot_v.setXRange(x[0], x[-1], padding=0)
        self._plot_v.enableAutoRange(axis="y")
        self._plot_u.enableAutoRange(axis="y")

        self._start_spin.blockSignals(True)
        self._start_spin.setValue(actual_start)
        self._start_spin.blockSignals(False)

    # ── Private: live ─────────────────────────────────────────────────────────

    def _live_refresh(self):
        if not self._bin_paths:
            return

        n_lag = self._lag_spin.value()
        spf = self._profile.samples_per_frame
        cpp = self._profile.n_chan_per_port
        packed_data_bytes = spf * cpp * 3
        win_pkts = max(1, self._live_win_spin.value() // spf)

        port_pkts = []
        for path in self._bin_paths:
            if not os.path.exists(path):
                return
            try:
                with PacketReader(
                    path, swmr=path.endswith((".h5", ".hdf5")), profile=self._profile
                ) as reader:
                    reader.refresh()
                    port_pkts.append(reader.n_packets)
                    self._profile = reader.profile
            except (OSError, KeyError, ValueError):
                return

        min_head = min(port_pkts)
        aligned_end = min_head - n_lag
        if aligned_end < 1:
            return
        spf = self._profile.samples_per_frame
        cpp = self._profile.n_chan_per_port
        packed_data_bytes = spf * cpp * 3
        aligned_start = max(0, aligned_end - win_pkts)
        n_pkts = aligned_end - aligned_start
        if n_pkts < 1:
            return

        raw_ports: list[np.ndarray] = []
        for path, total_pkts in zip(self._bin_paths, port_pkts):
            p_start = aligned_start
            p_end = aligned_end
            try:
                with PacketReader(
                    path, swmr=path.endswith((".h5", ".hdf5")), profile=self._profile
                ) as reader:
                    reader.refresh()
                    seqs = reader.frame_seqs(p_start, p_end)
                    if len(seqs) > 1 and np.any((np.diff(seqs) % 256) != 1):
                        return
                    words = reader.read_words(p_start, p_end)
            except (ValueError, OSError, KeyError):
                return

            payload_words = words[:, PACKED_HEADER_WORDS:].astype(">u4", copy=False)
            payload_bytes = payload_words.view(np.uint8).reshape(
                n_pkts, packed_data_bytes
            )
            byte_codes = payload_bytes.reshape(n_pkts, spf, cpp, 3).astype(np.int32)
            unsigned_codes = (
                (byte_codes[:, :, :, 0] << 16)
                | (byte_codes[:, :, :, 1] << 8)
                | byte_codes[:, :, :, 2]
            )
            codes = unsigned_codes.astype(np.int32)
            codes[codes >= 0x800000] -= 0x1000000
            raw_ports.append(codes.reshape(-1, cpp))

        if not raw_ports:
            return

        x = np.arange(n_pkts * spf)

        if self._demod_mode:
            m = self._display_mode
            primary_ports, secondary_ports = [], []
            for codes in raw_ports:
                f64 = codes.astype(np.float64)
                half = cpp // 2
                I, Q = f64[:, :half], f64[:, half:]
                amp = np.sqrt(I**2 + Q**2) * LSB
                phase = np.arctan2(Q, I)
                if m == "iq":
                    primary_ports.append(f64 * LSB)
                    secondary_ports.append(None)
                elif m == "recovered":
                    rec, _ = _track_and_recover(I, Q, _PLL_CUTOFF_DEFAULT)
                    rec = rec * LSB
                    primary_ports.append(rec)
                    secondary_ports.append(None)
                elif m == "amplitude":
                    primary_ports.append(amp)
                    secondary_ports.append(None)
                elif m == "phase":
                    primary_ports.append(phase)
                    secondary_ports.append(None)
                elif m == "amplitude_phase":
                    primary_ports.append(amp)
                    secondary_ports.append(phase)
                elif m == "iq_amplitude":
                    primary_ports.append(f64 * LSB)
                    secondary_ports.append(amp)
                else:  # iq_phase
                    primary_ports.append(f64 * LSB)
                    secondary_ports.append(phase)

            v_arr = self._normalize(np.concatenate(primary_ports, axis=1))
            has_u = any(s is not None for s in secondary_ports)
            u_arr = np.concatenate(secondary_ports, axis=1) if has_u else None
            n_v = v_arr.shape[1]
            n_u = u_arr.shape[1] if u_arr is not None else 0
            if n_v != len(self._curves_v) or n_u != len(self._curves_u):
                self._plot_v.clear()
                self._plot_u.clear()
                v_map, u_map = self._demod_raw_ch_maps(n_v, n_u, self._display_mode)
                self._curves_v_raw_ch = v_map
                self._curves_u_raw_ch = u_map
                self._curves_v = [
                    self._plot_v.plot(
                        pen=self._pen_for(v_map[ch] if ch < len(v_map) else ch, n_v)
                    )
                    for ch in range(n_v)
                ]
                self._curves_u = [
                    self._plot_u.plot(
                        pen=self._pen_for(u_map[ch] if ch < len(u_map) else ch, n_u)
                    )
                    for ch in range(n_u)
                ]
            for ch, cv in enumerate(self._curves_v):
                cv.setData(x, v_arr[:, ch])
            for ch, cu in enumerate(self._curves_u):
                cu.setData(x, u_arr[:, ch])
        else:
            v_arr = np.concatenate(
                [(c.astype(np.float64) * LSB) for c in raw_ports], axis=1
            )
            u_arr = np.concatenate(
                [(c.astype(np.uint32) & 0x00FFFFFF) for c in raw_ports], axis=1
            )
            n_ch = v_arr.shape[1]
            if n_ch != len(self._curves_v):
                self._plot_v.clear()
                self._plot_u.clear()
                self._curves_v_raw_ch = list(range(n_ch))
                self._curves_u_raw_ch = list(range(n_ch))
                self._curves_v = [
                    self._plot_v.plot(pen=self._pen_for(ch, n_ch)) for ch in range(n_ch)
                ]
                self._curves_u = [
                    self._plot_u.plot(pen=self._pen_for(ch, n_ch)) for ch in range(n_ch)
                ]
            for ch, (cv, cu) in enumerate(zip(self._curves_v, self._curves_u)):
                cv.setData(x, v_arr[:, ch])
                cu.setData(x, u_arr[:, ch])

        self._apply_visibility()
        self._plot_v.enableAutoRange(axis="y")
        self._plot_u.enableAutoRange(axis="y")

        self._lbl_live_status.setText(f"Live  ·  {aligned_end:,} pkts")
