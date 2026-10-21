import os
import threading

from pixels.configs import get_interface_ips as _get_interface_ips
from pixels.configs import _find as _find_config_file
from pixels.daq.adx134.profiles import get_adx134_profile, profile_from_config
from pixels.gui.config_widget import PlotConfigWidget
from pixels.gui.state import AppState

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QHBoxLayout,
    QCheckBox,
    QScrollArea,
    QColorDialog,
    QRadioButton,
    QButtonGroup,
    QLabel,
    QToolButton,
    QSizePolicy,
    QFrame,
    QSpinBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QSettings

_SETTINGS_ORG = "pixels"
_SETTINGS_APP = "pixels-gui"
from PySide6.QtGui import QColor

ODR_OPTIONS_FALLBACK = [
    (0, "0: 1.040 MHz"),
    (1, "1: 756.302 kHz"),
    (2, "2: 353.915 kHz"),
    (3, "3: 250.267 kHz"),
    (4, "4: 500.000 kHz"),
]


class _DynamicStack(QStackedWidget):
    """QStackedWidget that sizes to the current page instead of the tallest page.

    Without this, switching to a device with few controls leaves a giant gap
    because the stacked widget reports the tallest child's sizeHint to the parent layout.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.currentChanged.connect(self._on_page_changed)
        # QStackedWidget default is Expanding; Preferred means take sizeHint, no more.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _on_page_changed(self, _) -> None:
        self.updateGeometry()
        win = self.window()
        if isinstance(win, QDialog):
            win.adjustSize()

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w else super().minimumSizeHint()


def _format_odr_hz(value_hz: float) -> str:
    if value_hz >= 1_000_000:
        return f"{value_hz / 1_000_000:.3f} MHz"
    if value_hz >= 1_000:
        return f"{value_hz / 1_000:.3f} kHz"
    return f"{value_hz:.3f} Hz"


def load_odr_options() -> list[tuple[int, str]]:
    path = _find_config_file("clocks.yaml")
    if path is None:
        return ODR_OPTIONS_FALLBACK
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        options: list[tuple[int, str]] = []
        for idx in sorted(int(k) for k in data):
            entry = data[idx]
            try:
                odr_total = (
                    int(entry["odr_high_time"])
                    + int(entry["odr_wait_first"])
                    + int(entry["odr_low_time"])
                    + int(entry["odr_wait_last"])
                    + 1
                )
                odr_hz = float(entry["dclk_hz"]) / odr_total
            except Exception:
                odr_hz = float(entry["odr_freq"])
            options.append((idx, f"{idx}: {_format_odr_hz(odr_hz)}"))
        return options or ODR_OPTIONS_FALLBACK
    except Exception:
        return ODR_OPTIONS_FALLBACK

# Cycles through matplotlib tab20 palette for default channel colors
_DEFAULT_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
    "#aec7e8",
    "#ffbb78",
    "#98df8a",
    "#ff9896",
    "#c5b0d5",
    "#c49c94",
    "#f7b6d2",
    "#c7c7c7",
    "#dbdb8d",
    "#9edae5",
]


class CollapsibleSection(QWidget):
    def __init__(self, title: str, content: QWidget, expanded: bool = True):
        super().__init__()
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._btn = QToolButton()
        self._btn.setCheckable(True)
        self._btn.setChecked(expanded)
        self._btn.setText(f" {title}")
        self._btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn.setStyleSheet(
            "QToolButton { font-weight: bold; font-size: 11pt; border: none; padding: 4px 2px; }"
        )

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)

        self._content = content
        self._content.setVisible(expanded)

        outer.addWidget(self._btn)
        outer.addWidget(line)
        outer.addWidget(self._content)
        self.setLayout(outer)

        self._btn.toggled.connect(self._on_toggle)

    def _on_toggle(self, checked: bool):
        self._content.setVisible(checked)
        self._btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )


class _AdvancedSettingsDialog(QDialog):
    """Dialog for editing clock_gen and capture configuration fields."""

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Settings")
        self.setMinimumWidth(360)

        # data is the full _cfg_data dict
        _adx134 = data.get("adx134", {})
        _devs   = data.get("devices", {})

        outer = QVBoxLayout()

        # ── Device selector ───────────────────────────────────────────────────
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Device:"))
        self._device_sel = QComboBox()
        self._device_sel.addItems(["Global", "ADX134", "ADS1219", "RFSoC"])
        sel_row.addWidget(self._device_sel, 1)
        outer.addLayout(sel_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        self._stack = _DynamicStack()
        outer.addWidget(self._stack)

        # ── Page 0: Global ───────────────────────────────────────────────────
        _app = data.get("app", {})
        global_page = QWidget()
        global_layout = QFormLayout()
        global_layout.setContentsMargins(8, 8, 8, 8)
        self.debug_check = QCheckBox()
        self.debug_check.setChecked(bool(_app.get("debug", False)))
        global_layout.addRow("Debug logging:", self.debug_check)
        global_page.setLayout(global_layout)
        self._stack.addWidget(global_page)   # index 0

        # ── Page 1: ADX134 ────────────────────────────────────────────────────
        adx_page = QWidget()
        adx_layout = QVBoxLayout()
        adx_layout.setContentsMargins(0, 0, 0, 0)

        self._adx134_enabled = QCheckBox("Enabled")
        self._adx134_enabled.setChecked(bool(_devs.get("adx134", {}).get("enabled", True)))
        adx_layout.addWidget(self._adx134_enabled)

        # ADX134 Clock
        clock_form = QFormLayout()
        clock_form.setContentsMargins(8, 4, 8, 4)
        _clock = _adx134.get("clock_gen", {})

        self.odr_sel = QComboBox()
        for idx, label in load_odr_options():
            self.odr_sel.addItem(label, idx)
        current_odr = int(_clock.get("odr_config", 0))
        current_row = self.odr_sel.findData(current_odr)
        self.odr_sel.setCurrentIndex(current_row if current_row >= 0 else 0)
        clock_form.addRow("ODR Config:", self.odr_sel)

        clock_widget = QWidget()
        clock_widget.setLayout(clock_form)
        adx_layout.addWidget(CollapsibleSection("Clock", clock_widget))

        # ── ADX134 Capture ────────────────────────────────────────────────────
        cap_form = QFormLayout()
        cap_form.setContentsMargins(8, 4, 8, 4)
        _cap = _adx134.get("capture", {})
        _profile = profile_from_config(data)

        self.filename = QLineEdit(_cap.get("filename", ""))
        cap_form.addRow("Filename:", self.filename)

        self.n_chan_per_port = QSpinBox()
        self.n_chan_per_port.setRange(1, 1024)
        self.n_chan_per_port.setValue(_profile.n_chan_per_port)
        self.n_chan_per_port.setEnabled(False)
        cap_form.addRow("Chan/Port:", self.n_chan_per_port)

        self.samples_per_frame = QSpinBox()
        self.samples_per_frame.setRange(1, 10000)
        self.samples_per_frame.setValue(_profile.samples_per_frame)
        self.samples_per_frame.setEnabled(False)
        cap_form.addRow("Samples/Frame:", self.samples_per_frame)

        self.payload_bytes = QSpinBox()
        self.payload_bytes.setRange(1, 65535)
        self.payload_bytes.setValue(_profile.payload_bytes)
        self.payload_bytes.setEnabled(False)
        cap_form.addRow("Payload Bytes:", self.payload_bytes)

        self.scratch_format = QComboBox()
        self.scratch_format.addItems(["blosc_zstd", "hdf5_lzf", "bin"])
        scratch_default = str(_cap.get("scratch_format", "blosc_zstd"))
        self.scratch_format.setCurrentText(
            scratch_default if scratch_default in {"blosc_zstd", "hdf5_lzf", "bin"} else "blosc_zstd"
        )
        cap_form.addRow("Scratch Format:", self.scratch_format)

        self.write_batch = QSpinBox()
        self.write_batch.setRange(1, 100000)
        self.write_batch.setValue(int(_cap.get("write_batch", 512)))
        cap_form.addRow("Write Batch:", self.write_batch)

        self.v_ref = QDoubleSpinBox()
        self.v_ref.setRange(0.0, 20.0)
        self.v_ref.setDecimals(3)
        self.v_ref.setValue(float(_cap.get("v_ref", 4.096)))
        cap_form.addRow("V Ref:", self.v_ref)

        self.resolution = QSpinBox()
        self.resolution.setRange(8, 32)
        self.resolution.setValue(int(_cap.get("resolution", 24)))
        cap_form.addRow("Resolution (bits):", self.resolution)

        self.tx_enable_delay = QDoubleSpinBox()
        self.tx_enable_delay.setRange(0.0, 30.0)
        self.tx_enable_delay.setDecimals(1)
        self.tx_enable_delay.setSuffix(" s")
        self.tx_enable_delay.setValue(float(_cap.get("tx_enable_delay", 1.0)))
        cap_form.addRow("TX Enable Delay:", self.tx_enable_delay)

        self.capture_mode = QComboBox()
        self.capture_mode.addItem("Raw", "raw")
        self.capture_mode.addItem("Demod I/Q", "demod")
        mode_row = self.capture_mode.findData(_profile.mode)
        self.capture_mode.setCurrentIndex(mode_row if mode_row >= 0 else 0)
        self.capture_mode.currentIndexChanged.connect(self._apply_capture_profile)
        cap_form.addRow("Capture Mode:", self.capture_mode)

        cap_widget = QWidget()
        cap_widget.setLayout(cap_form)
        adx_layout.addWidget(CollapsibleSection("Capture", cap_widget))
        adx_layout.addStretch()
        adx_page.setLayout(adx_layout)
        self._stack.addWidget(adx_page)   # index 0

        # ── Page 1: ADS1219 ───────────────────────────────────────────────────
        from pixels.daq.ads1219.constants import DR_LABELS, SINGLE_ENDED_MASK
        _ads = data.get("ads1219", {})
        _mask = int(_ads.get("channel_mask", 0x03))
        _is_se = bool(_mask & SINGLE_ENDED_MASK)

        ads_layout = QVBoxLayout()
        ads_layout.setContentsMargins(8, 4, 8, 4)

        self._ads1219_enabled = QCheckBox("Enabled")
        self._ads1219_enabled.setChecked(bool(_devs.get("ads1219", {}).get("enabled", True)))
        ads_layout.addWidget(self._ads1219_enabled)
        ads_layout.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._ads_diff_radio = QRadioButton("Differential")
        self._ads_se_radio   = QRadioButton("Single-ended")
        self._ads_diff_radio.setChecked(not _is_se)
        self._ads_se_radio.setChecked(_is_se)
        mode_grp = QButtonGroup(self)
        mode_grp.addButton(self._ads_diff_radio)
        mode_grp.addButton(self._ads_se_radio)
        mode_row.addWidget(self._ads_diff_radio)
        mode_row.addWidget(self._ads_se_radio)
        mode_row.addStretch()
        ads_layout.addLayout(mode_row)

        self._diff_widget = QWidget()
        diff_grp = QHBoxLayout()
        diff_grp.setContentsMargins(0, 0, 0, 0)
        self._ch_diff = [
            (0, QCheckBox("AIN0-AIN1")),
            (1, QCheckBox("AIN2-AIN3")),
            (2, QCheckBox("AIN1-AIN2")),
        ]
        for mux, cb in self._ch_diff:
            cb.setChecked(bool(_mask & (1 << mux)))
            diff_grp.addWidget(cb)
        diff_grp.addStretch()
        self._diff_widget.setLayout(diff_grp)
        self._diff_widget.setVisible(not _is_se)
        ads_layout.addWidget(self._diff_widget)

        self._se_widget = QWidget()
        se_grp = QHBoxLayout()
        se_grp.setContentsMargins(0, 0, 0, 0)
        self._ch_se = [
            (3, QCheckBox("AIN0")),
            (4, QCheckBox("AIN1")),
            (5, QCheckBox("AIN2")),
            (6, QCheckBox("AIN3")),
        ]
        for mux, cb in self._ch_se:
            cb.setChecked(bool(_mask & (1 << mux)))
            se_grp.addWidget(cb)
        se_grp.addStretch()
        self._se_widget.setLayout(se_grp)
        self._se_widget.setVisible(_is_se)
        ads_layout.addWidget(self._se_widget)

        self._ads_diff_radio.toggled.connect(lambda on: (
            self._diff_widget.setVisible(on),
            self._se_widget.setVisible(not on),
        ))

        ads_form = QFormLayout()
        ads_form.setContentsMargins(0, 0, 0, 0)
        self.ads_gain = QComboBox()
        self.ads_gain.addItems(["1×", "4×"])
        self.ads_gain.setCurrentIndex(int(_ads.get("gain", 0)))
        ads_form.addRow("Gain:", self.ads_gain)
        self.ads_rate = QComboBox()
        for k, v in DR_LABELS.items():
            self.ads_rate.addItem(v, k)
        idx = self.ads_rate.findData(int(_ads.get("data_rate", 3)))
        self.ads_rate.setCurrentIndex(idx if idx >= 0 else 3)
        ads_form.addRow("Data Rate:", self.ads_rate)
        ads_layout.addLayout(ads_form)

        ads_widget = QWidget()
        ads_widget.setLayout(ads_layout)
        self._stack.addWidget(ads_widget)  # index 1

        # ── Page 2: RFSoC ─────────────────────────────────────────────────────
        rfsoc_layout = QVBoxLayout()
        rfsoc_layout.setContentsMargins(8, 4, 8, 4)
        self._rfsoc_enabled = QCheckBox("Enabled")
        self._rfsoc_enabled.setChecked(bool(_devs.get("rfsoc", {}).get("enabled", True)))
        rfsoc_layout.addWidget(self._rfsoc_enabled)
        rfsoc_layout.addStretch()
        rfsoc_widget = QWidget()
        rfsoc_widget.setLayout(rfsoc_layout)
        self._stack.addWidget(rfsoc_widget)  # index 2

        self._device_sel.currentIndexChanged.connect(self._stack.setCurrentIndex)

        # ── Dialog buttons ────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.setLayout(outer)

    def _apply_capture_profile(self) -> None:
        profile = get_adx134_profile(self.capture_mode.currentData())
        self.n_chan_per_port.setValue(profile.n_chan_per_port)
        self.samples_per_frame.setValue(profile.samples_per_frame)
        self.payload_bytes.setValue(profile.payload_bytes)

    def values(self) -> dict:
        return {
            "app": {
                "debug": self.debug_check.isChecked(),
            },
            "adx134": {
                "clock_gen": {
                    "odr_config": int(self.odr_sel.currentData()),
                },
                "capture": {
                    "filename":           self.filename.text(),
                    "mode":               self.capture_mode.currentData(),
                    "n_chan_per_port":     self.n_chan_per_port.value(),
                    "samples_per_frame":  self.samples_per_frame.value(),
                    "payload_bytes":      self.payload_bytes.value(),
                    "scratch_format":     self.scratch_format.currentText(),
                    "write_batch":        self.write_batch.value(),
                    "v_ref":              self.v_ref.value(),
                    "resolution":         self.resolution.value(),
                    "tx_enable_delay":    self.tx_enable_delay.value(),
                    "demod_mode":         self.capture_mode.currentData() == "demod",
                },
            },
            "ads1219": {
                "channel_mask": sum(
                    (1 << mux) for mux, cb in
                    (self._ch_se if self._ads_se_radio.isChecked() else self._ch_diff)
                    if cb.isChecked()
                ),
                "gain":      self.ads_gain.currentIndex(),
                "data_rate": self.ads_rate.currentData(),
            },
            "devices": {
                "adx134":  {"enabled": self._adx134_enabled.isChecked()},
                "ads1219": {"enabled": self._ads1219_enabled.isChecked()},
                "rfsoc":   {"enabled": self._rfsoc_enabled.isChecked()},
            },
        }


_FILTER_OPTIONS = [
    ("sinc3",         "SINC3"),
    ("sinc6",         "SINC6"),
    ("fir",           "FIR (Wideband)"),
    ("sinc3_50_60",   "SINC3 + 50/60 Hz Rej."),
]


class ContextualADCSettings(QWidget):
    """Stacked widget showing device-specific ADC settings based on active tab."""

    def __init__(self, widgets: "dict[str, QWidget]"):
        super().__init__()
        self._stack = _DynamicStack()
        self._device_index: dict[str, int] = {}
        self._setting_widgets: list = []
        for i, (key, widget) in enumerate(widgets.items()):
            self._stack.addWidget(widget)
            self._device_index[key] = i
            self._setting_widgets.append(widget)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self.setLayout(layout)

    def set_device(self, device: str) -> None:
        self._stack.setCurrentIndex(self._device_index.get(device, 0))

    def reload(self, cfg: dict) -> None:
        for w in self._setting_widgets:
            if hasattr(w, "load_from_cfg"):
                w.load_from_cfg(cfg)


class DataCfgWidget(QWidget):
    """Settings file + save directory widget, backed by AppState.

    Keeps legacy signals (debug_changed, demod_mode_changed, device_enabled_changed)
    so that MainWindow does not need to change its signal wiring.
    """

    debug_changed          = Signal(bool)
    demod_mode_changed     = Signal(bool)
    device_enabled_changed = Signal(str, bool)   # key, enabled

    def __init__(self, state: AppState):
        super().__init__()
        self._state = state

        layout = QFormLayout()

        cfg_row = QHBoxLayout()
        self.cfg_file = QLineEdit()
        self.cfg_file.setPlaceholderText("settings.yaml…")
        self.cfg_file.setReadOnly(True)
        self.cfg_browse_btn = QPushButton("Browse")
        self.advanced_btn = QToolButton()
        self.advanced_btn.setText("...")
        self.advanced_btn.setToolTip("Advanced Settings")
        self.advanced_btn.setFixedWidth(28)
        cfg_row.addWidget(self.cfg_file)
        cfg_row.addWidget(self.cfg_browse_btn)
        cfg_row.addWidget(self.advanced_btn)
        layout.addRow("Settings File:", cfg_row)

        dir_row = QHBoxLayout()
        self.save_dir = QComboBox()
        self.save_dir.setEditable(True)
        self.save_dir.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.save_dir.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.save_dir.lineEdit().setPlaceholderText("Select directory…")
        for p in self._load_dir_history():
            self.save_dir.addItem(p)
        _saved_dir = state.save_dir
        if _saved_dir:
            if self.save_dir.findText(_saved_dir) < 0:
                self.save_dir.insertItem(0, _saved_dir)
            self.save_dir.setCurrentText(_saved_dir)
        self.browse_btn = QPushButton("Browse")
        dir_row.addWidget(self.save_dir)
        dir_row.addWidget(self.browse_btn)
        layout.addRow("Save Dir:", dir_row)

        self.advanced_settings_btn = QPushButton("Advanced Settings")
        layout.addRow(self.advanced_settings_btn)

        # Kept for backward compatibility — SidebarWidget wires this
        self.on_settings_loaded: callable | None = None

        self.setLayout(layout)
        self.cfg_browse_btn.clicked.connect(self._browse_cfg)
        self.browse_btn.clicked.connect(self._browse_dir)
        self.advanced_btn.clicked.connect(self.open_advanced_settings)
        self.advanced_settings_btn.clicked.connect(self.open_advanced_settings)
        self.save_dir.activated.connect(self._on_dir_selected)

        # Re-emit derived signals whenever state changes
        state.settings_changed.connect(self._on_state_changed)
        state.device_enabled_changed.connect(self.device_enabled_changed)
        state.save_dir_changed.connect(self.save_dir.setCurrentText)

    # ── Public properties (legacy compat) ─────────────────────────────────────

    @property
    def _cfg_data(self) -> dict:
        """Live view of the shared config dict (for backward compatibility)."""
        return self._state.config

    @property
    def odr_index(self) -> int:
        return int(self._state.get("adx134", "clock_gen", "odr_config", default=0))

    # ── File loading ──────────────────────────────────────────────────────────

    def _browse_cfg(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select settings.yaml", "", "YAML files (*.yaml *.yml);;All files (*)"
        )
        if path:
            self._load_settings(path)

    def _load_settings(self, path: str):
        try:
            self._state.load_yaml(path)
            self.cfg_file.setText(path)
            QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("last_settings_file", path)
            if self.on_settings_loaded:
                self.on_settings_loaded(self._state.config)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load Failed", str(e))

    def _browse_dir(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Select Save Directory")
        if path:
            self._commit_save_dir(path)

    def _on_dir_selected(self, index: int) -> None:
        path = self.save_dir.itemText(index).strip()
        if path:
            self._commit_save_dir(path)

    def _commit_save_dir(self, path: str) -> None:
        """Add path to history, update combo, persist, and notify state."""
        history = [path] + [
            self.save_dir.itemText(i)
            for i in range(self.save_dir.count())
            if self.save_dir.itemText(i) != path
        ]
        history = history[:8]
        self.save_dir.blockSignals(True)
        self.save_dir.clear()
        self.save_dir.addItems(history)
        self.save_dir.setCurrentIndex(0)
        self.save_dir.blockSignals(False)
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("save_dir_history", history)
        # Keep legacy key in sync for load_previous_configs fallback
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue("last_save_dir", path)
        self._state.save_dir = path

    @staticmethod
    def _load_dir_history() -> list[str]:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        raw = s.value("save_dir_history", [])
        if isinstance(raw, list):
            return [p for p in raw if p]
        return [raw] if raw else []

    def apply_save_dir(self, path: str) -> None:
        if not path:
            return
        if self.save_dir.findText(path) < 0:
            self.save_dir.insertItem(0, path)
        self.save_dir.setCurrentText(path)

    def open_advanced_settings(self):
        dlg = _AdvancedSettingsDialog(self._state.config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            self._state.update(vals)
            # Derived signals are emitted by _on_state_changed

    # ── State listener ────────────────────────────────────────────────────────

    def _on_state_changed(self, config: dict) -> None:
        self.debug_changed.emit(bool(config.get("app", {}).get("debug", False)))
        self.demod_mode_changed.emit(profile_from_config(config).demod_mode)
        save_dir = config.get("save_dir", "")
        if save_dir and self.save_dir.text() != save_dir:
            self.save_dir.setText(save_dir)


class _PortRow(QWidget):
    """One ethernet port: interface dropdown + host IP (auto-filled) + Remote IP:port."""

    def __init__(self, label: str):
        super().__init__()
        self._iface_ips: dict[str, str] = {}

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 2, 0, 4)
        outer.setSpacing(2)

        hdr_row = QHBoxLayout()
        hdr_row.addWidget(QLabel(f"<b>{label}</b>"))
        self._warn_label = QLabel("duplicate interface")
        self._warn_label.setStyleSheet("color: #e65100; font-size: 10px;")
        self._warn_label.setVisible(False)
        hdr_row.addWidget(self._warn_label)
        hdr_row.addStretch()
        outer.addLayout(hdr_row)

        form = QFormLayout()
        form.setContentsMargins(8, 0, 0, 0)
        form.setVerticalSpacing(3)

        self.iface_sel = QComboBox()
        self.iface_sel.setMinimumWidth(90)
        form.addRow("Interface:", self.iface_sel)

        self.host_ip = QLineEdit()
        self.host_ip.setPlaceholderText("Host IP…")
        form.addRow("Host IP:", self.host_ip)

        remote_row = QHBoxLayout()
        self.remote_ip = QLineEdit()
        self.remote_ip.setPlaceholderText("Remote IP…")
        self.remote_port = QLineEdit()
        self.remote_port.setPlaceholderText("port…")
        self.remote_port.setFixedWidth(56)
        remote_row.addWidget(self.remote_ip)
        remote_row.addWidget(QLabel(":"))
        remote_row.addWidget(self.remote_port)
        form.addRow("Remote:", remote_row)

        outer.addLayout(form)
        self.setLayout(outer)

        self.iface_sel.currentTextChanged.connect(self._on_iface_changed)

    def refresh(self, iface_ips: dict[str, str] | None = None):
        self._iface_ips = iface_ips if iface_ips is not None else _get_interface_ips()
        current = self.iface_sel.currentText()
        self.iface_sel.blockSignals(True)
        self.iface_sel.clear()
        self.iface_sel.addItems(list(self._iface_ips.keys()))
        if current in self._iface_ips:
            self.iface_sel.setCurrentText(current)
        self.iface_sel.blockSignals(False)
        self._on_iface_changed(self.iface_sel.currentText())

    def _on_iface_changed(self, name: str):
        self.host_ip.setText(self._iface_ips.get(name, ""))

    def set_duplicate(self, is_dup: bool):
        self._warn_label.setVisible(is_dup)
        self.iface_sel.setStyleSheet(
            "QComboBox { border: 1px solid #e65100; }" if is_dup else ""
        )


class _ADS1219Row(QWidget):
    """ADS1219 serial port settings for the connection dialog (port/baud only)."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(3)

        layout.addWidget(QLabel("<b>ADS1219 Serial</b>"))

        conn_row = QHBoxLayout()
        self.port_sel = QComboBox()
        self.port_sel.setEditable(True)
        self.port_sel.setMinimumWidth(120)
        self.port_sel.setPlaceholderText("Port…")
        self._refresh_btn = QPushButton("⟳")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.clicked.connect(self._refresh_ports)
        self.baud_sel = QComboBox()
        self.baud_sel.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self.baud_sel.setCurrentText("115200")
        conn_row.addWidget(QLabel("Port:"))
        conn_row.addWidget(self.port_sel, 1)
        conn_row.addWidget(self._refresh_btn)
        conn_row.addWidget(QLabel("Baud:"))
        conn_row.addWidget(self.baud_sel)
        layout.addLayout(conn_row)

        self.setLayout(layout)
        self._refresh_ports()

    def _refresh_ports(self):
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except ImportError:
            ports = []
        current = self.port_sel.currentText()
        self.port_sel.clear()
        self.port_sel.addItems(ports)
        if current in ports:
            self.port_sel.setCurrentText(current)

    def get_config(self) -> dict | None:
        port = self.port_sel.currentText().strip()
        if not port:
            return None
        return {
            "port": port,
            "baud": int(self.baud_sel.currentText()),
        }

    def load_from_dict(self, cfg: dict):
        port = cfg.get("port", "")
        if port:
            self.port_sel.setCurrentText(port)
        baud = str(cfg.get("baud", "115200"))
        if self.baud_sel.findText(baud) >= 0:
            self.baud_sel.setCurrentText(baud)


class _ConnectionDialog(QDialog):
    """Modal dialog for the ADX134 10G link settings + ADS1219 serial + RFSoC.

    ADX134 has a single connection: frame k of all four data streams arrives in
    one jumbo datagram on it.  ``ports`` stays a list (of one row, saved as
    ``eth0``) so callers that iterate it keep working.
    """

    _DEVICE_KEYS = ["adx134", "ads1219", "rfsoc"]  # matches combo / stack order

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state = state
        self.setWindowTitle("Connection Configuration")
        self.setMinimumWidth(420)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Device selector ───────────────────────────────────────────────────
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Device:"))
        self._device_sel = QComboBox()
        self._device_sel.addItems(["ADX134 (UDP)", "ADS1219 (Serial)", "RFSoC (TCP/UDP)"])
        sel_row.addWidget(self._device_sel, 1)
        layout.addLayout(sel_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        self._stack = _DynamicStack()
        layout.addWidget(self._stack)

        # ── Page 0: ADX134 UDP ────────────────────────────────────────────────
        adx_page = QWidget()
        adx_layout = QVBoxLayout()
        adx_layout.setContentsMargins(0, 0, 0, 0)
        adx_layout.setSpacing(4)

        self.ports: list[_PortRow] = [_PortRow("10G link")]
        adx_layout.addWidget(self.ports[0])
        note = QLabel(
            "All four data streams arrive on this port in one jumbo datagram "
            "(host NIC MTU 9000 / Jumbo Packet 9014)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 10px;")
        adx_layout.addWidget(note)

        refresh_row = QHBoxLayout()
        refresh_btn = QPushButton("⟳ Refresh Interfaces")
        refresh_btn.clicked.connect(self._refresh_all)
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch()
        adx_layout.addLayout(refresh_row)
        adx_layout.addStretch()
        adx_page.setLayout(adx_layout)
        self._stack.addWidget(adx_page)   # index 0

        # ── Page 1: ADS1219 Serial ────────────────────────────────────────────
        self.ads1219_row = _ADS1219Row()
        self._stack.addWidget(self.ads1219_row)  # index 1

        # ── Page 2: RFSoC TCP/UDP ─────────────────────────────────────────────
        self.rfsoc_row = RFSoCSettingsWidget()
        self._stack.addWidget(self.rfsoc_row)   # index 2

        self._device_sel.currentIndexChanged.connect(self._stack.setCurrentIndex)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

        self._apply_device_visibility()
        state.settings_changed.connect(lambda _cfg: self._apply_device_visibility())
        state.device_enabled_changed.connect(lambda _k, _e: self._apply_device_visibility())

    def _apply_device_visibility(self) -> None:
        enabled = self._state.enabled_devices
        model = self._device_sel.model()
        for i, key in enumerate(self._DEVICE_KEYS):
            item = model.item(i)
            if item is not None:
                item.setEnabled(key in enabled)
        current_key = self._DEVICE_KEYS[self._device_sel.currentIndex()]
        if current_key not in enabled:
            for i, key in enumerate(self._DEVICE_KEYS):
                if key in enabled:
                    self._device_sel.setCurrentIndex(i)
                    break

    def _refresh_all(self):
        iface_ips = _get_interface_ips()
        for row in self.ports:
            row.refresh(iface_ips)

    def load_from_yaml(self, cfg: dict):
        iface_ips = _get_interface_ips()
        for i, row in enumerate(self.ports):
            entry = cfg.get(f"eth{i}", {})
            row.refresh(iface_ips)
            iface_name = entry.get("interface", "")
            if iface_name in [
                row.iface_sel.itemText(j) for j in range(row.iface_sel.count())
            ]:
                row.iface_sel.setCurrentText(iface_name)
            host_ip = entry.get("host_ip", "auto")
            if host_ip and host_ip != "auto":
                row.host_ip.setText(host_ip)
            row.remote_ip.setText(str(entry.get("remote_ip", "")))
            row.remote_port.setText(str(entry.get("port", "")))
        if "ads1219" in cfg:
            self.ads1219_row.load_from_dict(cfg["ads1219"])
        if "rfsoc" in cfg:
            self.rfsoc_row.load_from_cfg({"rfsoc": cfg["rfsoc"]})

    def get_ads1219_config(self) -> dict | None:
        return self.ads1219_row.get_config()

    def get_rfsoc_config(self) -> dict:
        return {
            "bind": self.rfsoc_row.bind,
            "port": self.rfsoc_row.port,
            "mode": self.rfsoc_row.mode,
        }

    def summary(self) -> str:
        row = self.ports[0]
        if row.remote_ip.text().strip() and row.remote_port.text().strip():
            return f"10G link: {row.remote_ip.text().strip()}:{row.remote_port.text().strip()}"
        return "10G link not configured"


class ConnectionCfgWidget(QWidget):
    def __init__(self):
        super().__init__()

        layout = QFormLayout()

        self._cfg_ips_btn = QPushButton("Configure Connection…")
        layout.addRow(self._cfg_ips_btn)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addRow(self._summary_label)

        self._dialog: _ConnectionDialog | None = None

        self.setLayout(layout)

    def wire_state(self, state: AppState) -> None:
        """Connect this widget to AppState so connections auto-reload on settings load."""
        self._dialog = _ConnectionDialog(state, parent=self)
        self._cfg_ips_btn.clicked.connect(self._open_dialog)
        state.settings_changed.connect(
            lambda cfg: self._reload_from_conn(cfg.get("connections", {}))
        )
        # Initial load from current state
        self._reload_from_conn(state.connections)

    def _reload_from_conn(self, conn: dict) -> None:
        """Load from a connections block — handles both nested {adx134:{...}, ads1219:{...}}
        and legacy flat {eth0:{...}, ads1219:{...}} formats."""
        adx = conn.get("adx134", None)
        if adx is not None:
            # new nested format
            self._dialog.load_from_yaml(adx)
            ads_row = conn.get("ads1219", {})
            if ads_row:
                self._dialog.ads1219_row.load_from_dict(ads_row)
            rfsoc_row = conn.get("rfsoc", {})
            if rfsoc_row:
                self._dialog.rfsoc_row.load_from_cfg({"rfsoc": rfsoc_row})
        elif conn:
            # legacy flat format — pass whole dict to dialog
            self._dialog.load_from_yaml(conn)
        self._summary_label.setText(self._dialog.summary())

    def _open_dialog(self):
        self._dialog.exec()
        self._summary_label.setText(self._dialog.summary())

    @property
    def ports(self) -> list[_PortRow]:
        return self._dialog.ports

    def get_connections(self):
        from pixels.control.comms import Connection

        conns = []
        for row in self._dialog.ports:
            remote_ip = row.remote_ip.text().strip()
            port_str = row.remote_port.text().strip()
            host_ip = row.host_ip.text().strip()
            if remote_ip and port_str:
                try:
                    conns.append(
                        Connection(
                            fpga_ip=remote_ip,
                            fpga_port=int(port_str),
                            host_ip=host_ip,
                        )
                    )
                except ValueError:
                    pass
        return conns

    def get_ads1219_config(self) -> dict | None:
        return self._dialog.get_ads1219_config()

    def get_rfsoc_config(self) -> dict:
        return self._dialog.get_rfsoc_config()


class ChannelRow(QWidget):
    changed = Signal()

    def __init__(self, index: int, color: str):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)

        self.checkbox = QCheckBox(f"CH{index:03d}")
        self.checkbox.setChecked(True)
        self._color = QColor(color)

        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(18, 18)
        self._update_swatch()

        layout.addWidget(self.checkbox)
        layout.addStretch()
        layout.addWidget(self.color_btn)
        self.setLayout(layout)

        self.color_btn.clicked.connect(self._pick_color)
        self.checkbox.toggled.connect(self.changed)

    def _pick_color(self):
        color = QColorDialog.getColor(self._color, self)
        if color.isValid():
            self._color = color
            self._update_swatch()
            self.changed.emit()

    def _update_swatch(self):
        self.color_btn.setStyleSheet(
            f"background-color: {self._color.name()}; border: 1px solid #666; border-radius: 2px;"
        )

    @property
    def enabled(self) -> bool:
        return self.checkbox.isChecked()

    @property
    def color(self) -> QColor:
        return self._color


_PORT_COLORS = ["#1f77b4", "#2ca02c", "#d62728", "#9467bd"]


_SPATIAL_COLORMAPS = ["viridis", "plasma", "inferno", "hot", "CET-L9", "grey"]
_SPATIAL_METRICS   = ["Peak", "RMS", "Mean"]


class _ADX134WaveformPage(PlotConfigWidget):
    """Waveform page for ADX134 — schema-driven controls + ports + channel rows."""

    channels_changed    = Signal()
    port_toggled        = Signal(int, bool)
    port_show_all       = Signal()
    carrier_vpp_changed = Signal(float)
    NUM_CHANNELS = 112

    def __init__(self):
        from pixels.gui.plot_schemas import ADX134_WAVEFORM_SCHEMA
        super().__init__(ADX134_WAVEFORM_SCHEMA)
        self._demod_active = False

        carrier_row = QHBoxLayout()
        carrier_row.addWidget(QLabel("Carrier Vpp:"))
        self._carrier_vpp_spin = QDoubleSpinBox()
        self._carrier_vpp_spin.setRange(0.0, 100.0)
        self._carrier_vpp_spin.setDecimals(3)
        self._carrier_vpp_spin.setValue(0.0)
        self._carrier_vpp_spin.setSuffix(" V")
        self._carrier_vpp_spin.setSpecialValueText("Off")
        carrier_row.addWidget(self._carrier_vpp_spin, 1)
        self._carrier_vpp_widget = QWidget()
        self._carrier_vpp_widget.setLayout(carrier_row)
        self._carrier_vpp_widget.setVisible(False)
        self._outer_layout.addWidget(self._carrier_vpp_widget)
        self._carrier_vpp_spin.editingFinished.connect(
            lambda: self.carrier_vpp_changed.emit(self._carrier_vpp_spin.value())
        )

        self._port_btns: list[QPushButton] = []
        self._ports_layout = QVBoxLayout()
        self._ports_layout.setSpacing(2)
        self._ports_layout.setContentsMargins(0, 2, 0, 2)
        self._ports_inner = QWidget()
        self._ports_inner.setLayout(self._ports_layout)
        self._ports_section = CollapsibleSection("Ports", self._ports_inner, expanded=True)
        self._ports_section.setVisible(False)
        self._outer_layout.addWidget(self._ports_section)

        btn_row = QHBoxLayout()
        sel_all_btn   = QPushButton("All")
        desel_all_btn = QPushButton("None")
        btn_row.addWidget(sel_all_btn)
        btn_row.addWidget(desel_all_btn)
        btn_row.addStretch()
        self._outer_layout.addLayout(btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        ch_layout = QVBoxLayout()
        ch_layout.setSpacing(0)
        ch_layout.setContentsMargins(0, 0, 0, 0)
        self._channels_layout = ch_layout
        self.channels: list[ChannelRow] = []
        for i in range(self.NUM_CHANNELS):
            row = ChannelRow(i, _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)])
            row.changed.connect(self.channels_changed)
            self.channels.append(row)
            ch_layout.addWidget(row)
        container.setLayout(ch_layout)
        scroll.setWidget(container)
        self._outer_layout.addWidget(scroll)

        sel_all_btn.clicked.connect(lambda: self._set_all(True))
        desel_all_btn.clicked.connect(lambda: self._set_all(False))

    def set_demod_mode(self, enabled: bool) -> None:
        from pixels.gui.plot_schemas import ADX134_RAW_DISPLAY_OPTIONS, ADX134_DEMOD_DISPLAY_OPTIONS
        # Channel rows are indexed by raw ADC channel in every mode (demod I/Q share a row)
        self._ensure_channel_rows(4 * get_adx134_profile("raw").n_chan_per_port)
        if enabled == self._demod_active:
            return
        self._demod_active = enabled
        self.set_display_options(
            ADX134_DEMOD_DISPLAY_OPTIONS if enabled else ADX134_RAW_DISPLAY_OPTIONS
        )
        self._carrier_vpp_widget.setVisible(enabled)

    def _ensure_channel_rows(self, count: int) -> None:
        while len(self.channels) < count:
            idx = len(self.channels)
            row = ChannelRow(idx, _DEFAULT_COLORS[idx % len(_DEFAULT_COLORS)])
            row.changed.connect(self.channels_changed)
            self.channels.append(row)
            self._channels_layout.addWidget(row)

    def build_port_buttons(self, n_ports: int, cpp: int) -> None:
        if self._demod_active:
            cpp //= 2  # demod cpp counts I and Q; rows are per raw channel
        self._ensure_channel_rows(n_ports * cpp)
        self._cpp_for_ports = cpp
        for btn in self._port_btns:
            btn.setParent(None)
        self._port_btns.clear()
        for i in range(n_ports):
            color = _PORT_COLORS[i % len(_PORT_COLORS)]
            btn = QPushButton(f"eth{i}  (ch{i * cpp}–{(i + 1) * cpp - 1})")
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white; "
                f"border: none; border-radius: 3px; padding: 3px 6px; }}"
                f"QPushButton:!checked {{ background-color: #888; }}"
            )
            btn.toggled.connect(lambda checked, idx=i: self._on_port_btn_toggled(idx, checked))
            self._port_btns.append(btn)
            self._ports_layout.addWidget(btn)
        if n_ports:
            show_all = QPushButton("Show All Ports")
            show_all.clicked.connect(self._show_all_ports)
            self._port_btns.append(show_all)
            self._ports_layout.addWidget(show_all)
        self._ports_section.setVisible(n_ports > 0)

    def _on_port_btn_toggled(self, port_idx: int, checked: bool) -> None:
        cpp = getattr(self, "_cpp_for_ports", 0)
        start = port_idx * cpp
        for ch_idx in range(start, start + cpp):
            if ch_idx < len(self.channels):
                row = self.channels[ch_idx]
                row.checkbox.blockSignals(True)
                row.checkbox.setChecked(checked)
                row.checkbox.blockSignals(False)
        self.channels_changed.emit()
        self.port_toggled.emit(port_idx, checked)

    def _show_all_ports(self) -> None:
        for btn in self._port_btns:
            if btn.isCheckable():
                btn.setChecked(True)
        self.port_show_all.emit()

    def _set_all(self, checked: bool) -> None:
        for ch in self.channels:
            ch.checkbox.blockSignals(True)
            ch.checkbox.setChecked(checked)
            ch.checkbox.blockSignals(False)
        self.channels_changed.emit()


class _ADX134SpatialPage(PlotConfigWidget):
    """Spatial page for ADX134 — schema-driven mode/browse + metric/colormap/range."""

    spatial_map_path_changed = Signal(str)
    spatial_metric_changed   = Signal(str)
    spatial_window_changed   = Signal(int)
    spatial_colormap_changed = Signal(str)
    spatial_range_changed    = Signal(bool, float, float)

    def __init__(self):
        from pixels.gui.plot_schemas import ADX134_SPATIAL_SCHEMA
        super().__init__(ADX134_SPATIAL_SCHEMA)

        # Spatial browse opens a YAML dialog and handles everything internally
        self.browse_clicked.connect(self._browse_map)

        for _lbl, _attr, _cls in [
            ("Metric:",   "_metric_sel", QComboBox),
            ("Window:",   "_win_spin",   QSpinBox),
            ("Colormap:", "_cm_sel",     QComboBox),
            ("Range:",    "_range_sel",  QComboBox),
        ]:
            _row = QHBoxLayout()
            _row.addWidget(QLabel(_lbl))
            _w = _cls()
            setattr(self, _attr, _w)
            _row.addWidget(_w, 1)
            self._outer_layout.addLayout(_row)

        self._metric_sel.addItems(_SPATIAL_METRICS)
        self._win_spin.setRange(10, 1_000_000)
        self._win_spin.setValue(500)
        self._win_spin.setSuffix(" smp")
        self._cm_sel.addItems(_SPATIAL_COLORMAPS)
        self._range_sel.addItems(["Auto", "Manual"])

        self._manual_range = QWidget()
        _mr = QHBoxLayout()
        _mr.setContentsMargins(0, 0, 0, 0)
        self._range_min = QSpinBox()
        self._range_min.setRange(-10_000_000, 10_000_000)
        self._range_max = QSpinBox()
        self._range_max.setRange(-10_000_000, 10_000_000)
        self._range_max.setValue(1)
        _mr.addWidget(QLabel("Min:"))
        _mr.addWidget(self._range_min)
        _mr.addWidget(QLabel("Max:"))
        _mr.addWidget(self._range_max)
        self._manual_range.setLayout(_mr)
        self._manual_range.setVisible(False)
        self._outer_layout.addWidget(self._manual_range)

        self._metric_sel.currentTextChanged.connect(self.spatial_metric_changed)
        self._win_spin.editingFinished.connect(
            lambda: self.spatial_window_changed.emit(self._win_spin.value())
        )
        self._cm_sel.currentTextChanged.connect(self.spatial_colormap_changed)
        self._range_sel.currentTextChanged.connect(self._emit_range)
        self._range_min.editingFinished.connect(self._emit_range)
        self._range_max.editingFinished.connect(self._emit_range)

        last = QSettings().value("spatial/last_map_path", "")
        if last and os.path.exists(last):
            self.loaded_file.setText(os.path.basename(last))
            self.loaded_file.setToolTip(last)

    def _browse_map(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Channel Map", "configs/",
            "YAML files (*.yaml *.yml);;All files (*)"
        )
        if path:
            self.loaded_file.setText(os.path.basename(path))
            self.loaded_file.setToolTip(path)
            QSettings().setValue("spatial/last_map_path", path)
            self.spatial_map_path_changed.emit(path)

    def set_plot_mode_silent(self, mode: str) -> None:
        for btn in self._mode_btns:
            btn.blockSignals(True)
        for btn, val in zip(self._mode_btns, self._mode_values):
            btn.setChecked(val == mode)
        for btn in self._mode_btns:
            btn.blockSignals(False)

    def _emit_range(self) -> None:
        auto = self._range_sel.currentText() == "Auto"
        self._manual_range.setVisible(not auto)
        self.spatial_range_changed.emit(
            auto, float(self._range_min.value()), float(self._range_max.value())
        )


class ADX134PlotCfgWidget(QWidget):
    """ADX134 sidebar plot config — Waveform view and Spatial view in a stacked widget."""

    view_changed         = Signal(str)   # "waveform" | "spatial"
    # Waveform signals (forwarded from _waveform_page)
    plot_mode_changed    = Signal(str)
    display_mode_changed = Signal(str)
    browse_clicked       = Signal()
    channels_changed     = Signal()
    port_toggled         = Signal(int, bool)
    port_show_all        = Signal()
    carrier_vpp_changed  = Signal(float)
    # Spatial signals (forwarded from _spatial_page)
    spatial_plot_mode_changed = Signal(str)
    spatial_map_path_changed  = Signal(str)
    spatial_metric_changed    = Signal(str)
    spatial_window_changed    = Signal(int)
    spatial_colormap_changed  = Signal(str)
    spatial_range_changed     = Signal(bool, float, float)

    def __init__(self):
        super().__init__()

        _seg_style = (
            "QPushButton {"
            "  border: 1px solid #555; padding: 2px 12px; border-radius: 0;"
            "}"
            "QPushButton:checked {"
            "  background-color: #0d6efd; color: white; border-color: #0d6efd;"
            "}"
            "QPushButton:first-child { border-radius: 3px 0 0 3px; }"
            "QPushButton:last-child  { border-radius: 0 3px 3px 0; border-left: none; }"
        )
        self._waveform_btn = QPushButton("Waveform")
        self._spatial_btn  = QPushButton("Spatial")
        for _b in (self._waveform_btn, self._spatial_btn):
            _b.setCheckable(True)
            _b.setStyleSheet(_seg_style)
        self._waveform_btn.setChecked(True)
        _vgrp = QButtonGroup(self)
        _vgrp.setExclusive(True)
        _vgrp.addButton(self._waveform_btn)
        _vgrp.addButton(self._spatial_btn)
        view_row = QHBoxLayout()
        view_row.addWidget(self._waveform_btn)
        view_row.addWidget(self._spatial_btn)
        view_row.addStretch()

        self._waveform_page = _ADX134WaveformPage()
        self._spatial_page  = _ADX134SpatialPage()
        self._view_stack = _DynamicStack()
        self._view_stack.addWidget(self._waveform_page)   # index 0
        self._view_stack.addWidget(self._spatial_page)    # index 1

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        outer.addLayout(view_row)
        outer.addWidget(self._view_stack)
        self.setLayout(outer)

        self._waveform_btn.toggled.connect(self._on_view_toggled)

        # Forward waveform signals
        self._waveform_page.plot_mode_changed.connect(self.plot_mode_changed)
        self._waveform_page.display_mode_changed.connect(self.display_mode_changed)
        self._waveform_page.browse_clicked.connect(self.browse_clicked)
        self._waveform_page.channels_changed.connect(self.channels_changed)
        self._waveform_page.port_toggled.connect(self.port_toggled)
        self._waveform_page.port_show_all.connect(self.port_show_all)
        self._waveform_page.carrier_vpp_changed.connect(self.carrier_vpp_changed)

        # Forward spatial signals
        self._spatial_page.plot_mode_changed.connect(self.spatial_plot_mode_changed)
        self._spatial_page.spatial_map_path_changed.connect(self.spatial_map_path_changed)
        self._spatial_page.spatial_metric_changed.connect(self.spatial_metric_changed)
        self._spatial_page.spatial_window_changed.connect(self.spatial_window_changed)
        self._spatial_page.spatial_colormap_changed.connect(self.spatial_colormap_changed)
        self._spatial_page.spatial_range_changed.connect(self.spatial_range_changed)

    def _on_view_toggled(self, waveform_checked: bool) -> None:
        self._view_stack.setCurrentIndex(0 if waveform_checked else 1)
        self.view_changed.emit("waveform" if waveform_checked else "spatial")

    def set_view_silent(self, view: str) -> None:
        """Switch view without emitting view_changed (for reverse wiring from ADX134Tab)."""
        for btn in (self._waveform_btn, self._spatial_btn):
            btn.blockSignals(True)
        self._waveform_btn.setChecked(view == "waveform")
        self._spatial_btn.setChecked(view == "spatial")
        self._view_stack.setCurrentIndex(0 if view == "waveform" else 1)
        for btn in (self._waveform_btn, self._spatial_btn):
            btn.blockSignals(False)

    def set_spatial_plot_mode_silent(self, mode: str) -> None:
        self._spatial_page.set_plot_mode_silent(mode)

    # ── Convenience forwarding so existing MainWindow code works unchanged ─────

    @property
    def channels(self) -> list:
        return self._waveform_page.channels

    @property
    def current_mode(self) -> str:
        return self._waveform_page.current_mode

    @property
    def loaded_file(self):
        return self._waveform_page.loaded_file

    @property
    def live_plot(self):
        return self._waveform_page._mode_btns[0]

    @property
    def load_data(self):
        return self._waveform_page._mode_btns[1]

    def set_demod_mode(self, enabled: bool) -> None:
        self._waveform_page.set_demod_mode(enabled)

    def build_port_buttons(self, n_ports: int, cpp: int) -> None:
        self._waveform_page.build_port_buttons(n_ports, cpp)


_BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

_STYLE_CONNECTED = (
    "QPushButton { background-color: #2e7d32; color: white; "
    "border: none; border-radius: 3px; padding: 3px 8px; }"
    "QPushButton:hover { background-color: #388e3c; }"
)
_STYLE_DISCONNECTED = (
    "QPushButton { background-color: #b71c1c; color: white; "
    "border: none; border-radius: 3px; padding: 3px 8px; }"
    "QPushButton:hover { background-color: #c62828; }"
)


class ADS1219CfgWidget(QWidget):
    connected = Signal(object)    # emits ADS1219 instance
    disconnected = Signal()

    def __init__(self):
        super().__init__()
        from pixels.daq.ads1219.constants import MUX_LABELS, DR_LABELS

        self._adc = None
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(4)

        # Port / baud / connect
        port_row = QHBoxLayout()
        self._port_sel = QComboBox()
        self._port_sel.setEditable(True)
        self._port_sel.setMinimumWidth(110)
        self._port_sel.setPlaceholderText("Port…")
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_ports)
        self._baud_sel = QComboBox()
        self._baud_sel.addItems(_BAUD_RATES)
        self._baud_sel.setCurrentText("115200")
        port_row.addWidget(QLabel("Port:"))
        port_row.addWidget(self._port_sel, 1)
        port_row.addWidget(refresh_btn)
        layout.addLayout(port_row)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Baud:"))
        baud_row.addWidget(self._baud_sel, 1)
        layout.addLayout(baud_row)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setStyleSheet(_STYLE_DISCONNECTED)
        self._connect_btn.clicked.connect(self._toggle_connect)
        layout.addWidget(self._connect_btn)

        # Config
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setVerticalSpacing(3)

        self._mux_sel = QComboBox()
        for k, v in MUX_LABELS.items():
            self._mux_sel.addItem(v, k)
        form.addRow("MUX:", self._mux_sel)

        self._gain_sel = QComboBox()
        self._gain_sel.addItems(["1×", "4×"])
        form.addRow("Gain:", self._gain_sel)

        self._rate_sel = QComboBox()
        for k, v in DR_LABELS.items():
            self._rate_sel.addItem(v, k)
        self._rate_sel.setCurrentIndex(3)  # default 1000 SPS
        form.addRow("Data rate:", self._rate_sel)

        layout.addLayout(form)

        self._stream_btn = QPushButton("Start Streaming")
        self._stream_btn.setEnabled(False)
        self._stream_btn.clicked.connect(self._toggle_streaming)
        layout.addWidget(self._stream_btn)

        self.setLayout(layout)
        self._refresh_ports()
        self._streaming = False

    def _refresh_ports(self):
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except ImportError:
            ports = []
        current = self._port_sel.currentText()
        self._port_sel.clear()
        self._port_sel.addItems(ports)
        if current in ports:
            self._port_sel.setCurrentText(current)

    def _toggle_connect(self):
        if self._adc and self._adc.is_connected:
            if self._streaming:
                self._toggle_streaming()
            self._adc.disconnect()
            self._adc = None
            self._connect_btn.setText("Connect")
            self._connect_btn.setStyleSheet(_STYLE_DISCONNECTED)
            self._stream_btn.setEnabled(False)
            self.disconnected.emit()
        else:
            from pixels.daq.ads1219 import ADS1219, ADS1219Config
            port = self._port_sel.currentText().strip()
            baud = int(self._baud_sel.currentText())
            mux = self._mux_sel.currentData()
            gain = self._gain_sel.currentIndex()
            rate = self._rate_sel.currentData()
            try:
                cfg = ADS1219Config(mux=mux, gain=gain, data_rate=rate)
                self._adc = ADS1219(port, baud, config=cfg)
                self._adc.connect()
                self._adc.init()
                self._connect_btn.setText("Disconnect")
                self._connect_btn.setStyleSheet(_STYLE_CONNECTED)
                self._stream_btn.setEnabled(True)
                self.connected.emit(self._adc)
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Connection Failed", str(e))

    def _toggle_streaming(self):
        if not self._adc:
            return
        if self._streaming:
            self._adc.stop()
            self._stream_btn.setText("Start Streaming")
            self._streaming = False
        else:
            self._adc.start()
            self._stream_btn.setText("Stop Streaming")
            self._streaming = True


class ADS1219PlotCfgWidget(PlotConfigWidget):
    channels_changed = Signal(list)   # list of visible MUX ints

    _ALL_CHANNELS = [
        (0, "AIN0-AIN1"), (1, "AIN2-AIN3"), (2, "AIN1-AIN2"),
        (3, "AIN0"),      (4, "AIN1"),       (5, "AIN2"),      (6, "AIN3"),
    ]

    def __init__(self):
        from pixels.gui.plot_schemas import ADS1219_PLOT_SCHEMA
        super().__init__(ADS1219_PLOT_SCHEMA)

        _ch_colors = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
            "#9467bd", "#8c564b", "#e377c2",
        ]

        btn_row = QHBoxLayout()
        all_btn  = QPushButton("All")
        none_btn = QPushButton("None")
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        self._outer_layout.addLayout(btn_row)

        self._ch_rows: dict[int, ChannelRow] = {}
        ch_container = QWidget()
        ch_layout = QVBoxLayout()
        ch_layout.setSpacing(0)
        ch_layout.setContentsMargins(0, 0, 0, 0)
        for mux, label in self._ALL_CHANNELS:
            row = ChannelRow(mux, _ch_colors[mux % len(_ch_colors)])
            row.checkbox.setText(label)
            row.changed.connect(self._on_channels_changed)
            self._ch_rows[mux] = row
            ch_layout.addWidget(row)
        ch_layout.addStretch()
        ch_container.setLayout(ch_layout)

        scroll = QScrollArea()
        scroll.setWidget(ch_container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._outer_layout.addWidget(scroll)

        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))

    @property
    def visible_channels(self) -> list[int]:
        return [mux for mux, row in self._ch_rows.items() if row.enabled]

    def update_available_channels(self, active: list[int]) -> None:
        """Show only channels that are currently being scanned; hide the rest."""
        for mux, row in self._ch_rows.items():
            row.setVisible(mux in active)

    def _set_all(self, checked: bool) -> None:
        for row in self._ch_rows.values():
            if row.isVisible():
                row.checkbox.blockSignals(True)
                row.checkbox.setChecked(checked)
                row.checkbox.blockSignals(False)
        self.channels_changed.emit(self.visible_channels)

    def _on_channels_changed(self) -> None:
        self.channels_changed.emit(self.visible_channels)




class ADX134SetupWidget(QWidget):
    """Combines DataCfgWidget and ConnectionCfgWidget into one section."""

    def __init__(self, data_cfg: "DataCfgWidget", udp_cfg: "ConnectionCfgWidget"):
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(data_cfg)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        conn_lbl = QLabel("Connection Config")
        conn_lbl.setStyleSheet("font-weight: bold; padding: 4px 2px 2px 2px;")
        layout.addWidget(conn_lbl)
        layout.addWidget(udp_cfg)
        self.setLayout(layout)


class ContextualPlotCfg(QWidget):
    """A stacked widget that shows the plot config for the active device."""

    def __init__(self, widgets: "dict[str, QWidget]"):
        super().__init__()
        self._stack = _DynamicStack()
        self._device_index: dict[str, int] = {}
        for i, (key, widget) in enumerate(widgets.items()):
            self._stack.addWidget(widget)
            self._device_index[key] = i
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self.setLayout(layout)

    def set_device(self, device: str) -> None:
        self._stack.setCurrentIndex(self._device_index.get(device, 0))


_SPATIAL_COLORMAPS = ["viridis", "plasma", "inferno", "hot", "CET-L9", "grey"]
_SPATIAL_METRICS   = ["Peak", "RMS", "Mean"]


class RFSoCSettingsWidget(QWidget):
    """Connection settings for the RFSoC ADC stream receiver."""

    def __init__(self):
        super().__init__()
        layout = QFormLayout()
        layout.setContentsMargins(0, 0, 0, 4)

        self._bind = QLineEdit("0.0.0.0")
        layout.addRow("Bind address:", self._bind)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(5000)
        layout.addRow("Port:", self._port)

        mode_row = QHBoxLayout()
        self._tcp = QRadioButton("TCP")
        self._udp = QRadioButton("UDP")
        self._tcp.setChecked(True)
        mode_grp = QButtonGroup(self)
        mode_grp.addButton(self._tcp)
        mode_grp.addButton(self._udp)
        mode_row.addWidget(self._tcp)
        mode_row.addWidget(self._udp)
        mode_row.addStretch()
        layout.addRow("Mode:", mode_row)

        self.setLayout(layout)

    @property
    def bind(self) -> str:
        return self._bind.text().strip() or "0.0.0.0"

    @property
    def port(self) -> int:
        return self._port.value()

    @property
    def mode(self) -> str:
        return "tcp" if self._tcp.isChecked() else "udp"

    def load_from_cfg(self, cfg: dict) -> None:
        rfsoc = cfg.get("rfsoc", {})
        self._bind.setText(rfsoc.get("bind", "0.0.0.0"))
        self._port.blockSignals(True)
        self._port.setValue(int(rfsoc.get("port", 5000)))
        self._port.blockSignals(False)
        mode = rfsoc.get("mode", "tcp")
        self._tcp.setChecked(mode == "tcp")
        self._udp.setChecked(mode == "udp")


class RFSoCPlotCfgWidget(PlotConfigWidget):
    """Plot configuration for the RFSoC live view."""

    def __init__(self):
        from pixels.gui.plot_schemas import RFSOC_PLOT_SCHEMA
        super().__init__(RFSOC_PLOT_SCHEMA)


class SidebarWidget(QWidget):
    def __init__(self, state: AppState):
        super().__init__()

        from pixels.gui.config_widget import DeviceConfigWidget
        from pixels.gui.device_registry import DEVICE_REGISTRY

        self._state = state

        self.data_cfg = DataCfgWidget(state)
        self.conn_cfg = ConnectionCfgWidget()
        self.conn_cfg.wire_state(state)

        # Build ADC config and plot config widgets from the registry.
        _adc_widgets: dict = {}
        _plot_widgets: dict = {}
        for _desc in DEVICE_REGISTRY:
            if _desc.config_widget_factory is not None:
                _adc_widgets[_desc.key] = _desc.config_widget_factory(state)
            else:
                _lbl = _desc.configure_label or f"Configure {_desc.label}"
                _adc_widgets[_desc.key] = DeviceConfigWidget(
                    _desc.schema, state, configure_label=_lbl
                )
            if _desc.plot_widget_factory is not None:
                _plot_widgets[_desc.key] = _desc.plot_widget_factory()

        # Named references preserved for MainWindow signal wiring compatibility.
        self._adx134_adc  = _adc_widgets["adx134"]
        self._ads1219_adc = _adc_widgets["ads1219"]
        self._rfsoc_adc   = _adc_widgets["rfsoc"]
        self.adx134_plot_cfg  = _plot_widgets["adx134"]
        self.ads1219_plot_cfg = _plot_widgets["ads1219"]
        self.rfsoc_plot_cfg   = _plot_widgets["rfsoc"]

        self._adc_cfg_widget  = ContextualADCSettings(_adc_widgets)
        self._plot_cfg_widget = ContextualPlotCfg(_plot_widgets)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._setup_section = CollapsibleSection(
            "Global Setup", ADX134SetupWidget(self.data_cfg, self.conn_cfg)
        )
        self._adc_section   = CollapsibleSection("ADC Settings", self._adc_cfg_widget)
        self._plot_section  = CollapsibleSection("Plot Config", self._plot_cfg_widget)

        layout.addWidget(self._setup_section)
        layout.addWidget(self._adc_section)
        layout.addWidget(self._plot_section)
        layout.addStretch()
        container.setLayout(layout)

        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)
        self.setMinimumWidth(420)

        # Start in ADX134 mode
        self.set_active_device("adx134")

    @property
    def rfsoc_cfg(self) -> "RFSoCSettingsWidget":
        return self._rfsoc_adc

    def set_active_device(self, device: str) -> None:
        self._plot_cfg_widget.set_device(device)
        self._adc_cfg_widget.set_device(device)

    def load_previous_configs(self):
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)

        settings_path = s.value("last_settings_file", "")

        # Try to find a settings file in the last save dir if the direct path is gone
        if not settings_path or not os.path.exists(settings_path):
            save_dir = s.value("last_save_dir", "")
            candidate = os.path.join(save_dir, "settings.yaml") if save_dir else ""
            if candidate and os.path.exists(candidate):
                settings_path = candidate

        if settings_path and os.path.exists(settings_path):
            self.data_cfg._load_settings(settings_path)

        # Always restore the last save directory regardless of whether settings loaded
        save_dir = s.value("last_save_dir", "")
        if save_dir:
            self.data_cfg.apply_save_dir(save_dir)

    def open_advanced_settings(self) -> None:
        self.data_cfg.open_advanced_settings()
