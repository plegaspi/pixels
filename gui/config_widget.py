"""Generic schema-driven configuration widget.

DeviceConfigWidget accepts a list of FieldDef descriptors (from
device_schemas.py) and an AppState instance.  It builds the appropriate Qt
form fields automatically and keeps them in sync with the shared state.

Usage::

    from pixels.gui.config_widget import DeviceConfigWidget
    from pixels.gui.device_schemas import ADX134_SCHEMA

    widget = DeviceConfigWidget(ADX134_SCHEMA, state)
    widget.configure_clicked.connect(my_configure_handler)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixels.gui.device_schemas import (
    ChannelMaskField,
    CheckField,
    ComboField,
    FieldDef,
    FloatField,
    IntField,
)
from pixels.gui.state import AppState


def _get_nested(d: dict, dotted: str, default=None):
    keys = dotted.split(".")
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, None)
        if d is None:
            return default
    return d


def _set_nested(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


# ── Per-field renderer helpers ────────────────────────────────────────────────

class _ComboRenderer:
    def __init__(self, fdef: ComboField, state: AppState, layout: QFormLayout,
                 on_change_cb) -> None:
        self._key = fdef.key
        self._state = state
        self._widget = QComboBox()
        self._options: list[tuple[Any, str]] = []
        if fdef.options_loader is not None:
            self._options = fdef.options_loader()
        elif fdef.options is not None:
            self._options = fdef.options
        for val, label in self._options:
            self._widget.addItem(label, val)
        layout.addRow(f"{fdef.label}:", self._widget)
        self._widget.currentIndexChanged.connect(lambda _: on_change_cb())

    def load(self, config: dict) -> None:
        current = _get_nested(config, self._key)
        if current is None:
            return
        self._widget.blockSignals(True)
        for i in range(self._widget.count()):
            if self._widget.itemData(i) == current:
                self._widget.setCurrentIndex(i)
                break
        else:
            # Try string comparison as fallback
            for i in range(self._widget.count()):
                if str(self._widget.itemData(i)) == str(current):
                    self._widget.setCurrentIndex(i)
                    break
        self._widget.blockSignals(False)

    def apply(self, config: dict) -> None:
        _set_nested(config, self._key, self._widget.currentData())

    def qt_widget(self) -> QWidget:
        return self._widget


class _IntRenderer:
    def __init__(self, fdef: IntField, state: AppState, layout: QFormLayout,
                 on_change_cb) -> None:
        self._key = fdef.key
        self._widget = QSpinBox()
        self._widget.setRange(fdef.minimum, fdef.maximum)
        self._widget.setValue(fdef.default)
        layout.addRow(f"{fdef.label}:", self._widget)
        self._widget.valueChanged.connect(lambda _: on_change_cb())

    def load(self, config: dict) -> None:
        val = _get_nested(config, self._key)
        if val is not None:
            self._widget.blockSignals(True)
            self._widget.setValue(int(val))
            self._widget.blockSignals(False)

    def apply(self, config: dict) -> None:
        _set_nested(config, self._key, self._widget.value())


class _FloatRenderer:
    def __init__(self, fdef: FloatField, state: AppState, layout: QFormLayout,
                 on_change_cb) -> None:
        self._key = fdef.key
        self._widget = QDoubleSpinBox()
        self._widget.setRange(fdef.minimum, fdef.maximum)
        self._widget.setDecimals(fdef.decimals)
        self._widget.setValue(fdef.default)
        if fdef.suffix:
            self._widget.setSuffix(fdef.suffix)
        layout.addRow(f"{fdef.label}:", self._widget)
        self._widget.valueChanged.connect(lambda _: on_change_cb())

    def load(self, config: dict) -> None:
        val = _get_nested(config, self._key)
        if val is not None:
            self._widget.blockSignals(True)
            self._widget.setValue(float(val))
            self._widget.blockSignals(False)

    def apply(self, config: dict) -> None:
        _set_nested(config, self._key, self._widget.value())


class _CheckRenderer:
    def __init__(self, fdef: CheckField, state: AppState, layout: QFormLayout,
                 on_change_cb) -> None:
        self._key = fdef.key
        self._widget = QCheckBox()
        self._widget.setChecked(fdef.default)
        layout.addRow(f"{fdef.label}:", self._widget)
        self._widget.toggled.connect(lambda _: on_change_cb())

    def load(self, config: dict) -> None:
        val = _get_nested(config, self._key)
        if val is not None:
            self._widget.blockSignals(True)
            self._widget.setChecked(bool(val))
            self._widget.blockSignals(False)

    def apply(self, config: dict) -> None:
        _set_nested(config, self._key, self._widget.isChecked())


class _ChannelMaskRenderer:
    """Renders differential/single-ended mode radio + per-channel checkboxes."""

    def __init__(self, fdef: ChannelMaskField, state: AppState, layout: QFormLayout,
                 on_change_cb) -> None:
        self._key = fdef.key
        self._on_change = on_change_cb
        self._diff_channels = fdef.diff_channels
        self._se_channels = fdef.se_channels

        from pixels.daq.ads1219.constants import SINGLE_ENDED_MASK

        self._se_mask = SINGLE_ENDED_MASK

        # Mode radio row
        mode_row = QHBoxLayout()
        self._diff_radio = QRadioButton("Differential")
        self._se_radio = QRadioButton("Single-ended")
        self._diff_radio.setChecked(True)
        grp = QButtonGroup()
        grp.addButton(self._diff_radio)
        grp.addButton(self._se_radio)
        self._grp = grp
        mode_row.addWidget(self._diff_radio)
        mode_row.addWidget(self._se_radio)
        mode_row.addStretch()
        mode_container = QWidget()
        mode_container.setLayout(mode_row)
        layout.addRow(f"{fdef.label} mode:", mode_container)

        # Diff channels
        self._diff_widget = QWidget()
        diff_row = QHBoxLayout()
        diff_row.setContentsMargins(0, 0, 0, 0)
        self._ch_diff: list[tuple[int, QCheckBox]] = []
        for mux, lbl in fdef.diff_channels:
            cb = QCheckBox(lbl)
            diff_row.addWidget(cb)
            self._ch_diff.append((mux, cb))
        diff_row.addStretch()
        self._diff_widget.setLayout(diff_row)
        layout.addRow("", self._diff_widget)

        # SE channels
        self._se_widget = QWidget()
        se_row = QHBoxLayout()
        se_row.setContentsMargins(0, 0, 0, 0)
        self._ch_se: list[tuple[int, QCheckBox]] = []
        for mux, lbl in fdef.se_channels:
            cb = QCheckBox(lbl)
            se_row.addWidget(cb)
            self._ch_se.append((mux, cb))
        se_row.addStretch()
        self._se_widget.setLayout(se_row)
        self._se_widget.setVisible(False)
        layout.addRow("", self._se_widget)

        # Default: all diff channels selected so the initial mask is never 0.
        for _, cb in self._ch_diff:
            cb.setChecked(True)

        self._diff_radio.toggled.connect(self._on_mode)
        for _, cb in self._ch_diff + self._ch_se:
            cb.toggled.connect(lambda _: on_change_cb())

    def _on_mode(self, diff_checked: bool) -> None:
        self._diff_widget.setVisible(diff_checked)
        self._se_widget.setVisible(not diff_checked)
        # Select all channels for the newly active mode so the mask is never 0.
        active = self._ch_diff if diff_checked else self._ch_se
        for _, cb in active:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self._on_change()

    def _channel_mask(self) -> int:
        active = self._ch_diff if self._diff_radio.isChecked() else self._ch_se
        return sum((1 << mux) for mux, cb in active if cb.isChecked())

    def load(self, config: dict) -> None:
        mask = _get_nested(config, self._key)
        if mask is None:
            return
        mask = int(mask)
        is_se = bool(mask & self._se_mask)
        self._diff_radio.blockSignals(True)
        self._se_radio.blockSignals(True)
        self._diff_radio.setChecked(not is_se)
        self._se_radio.setChecked(is_se)
        self._diff_radio.blockSignals(False)
        self._se_radio.blockSignals(False)
        self._diff_widget.setVisible(not is_se)
        self._se_widget.setVisible(is_se)
        for mux, cb in self._ch_diff + self._ch_se:
            cb.blockSignals(True)
            cb.setChecked(bool(mask & (1 << mux)))
            cb.blockSignals(False)

    def apply(self, config: dict) -> None:
        _set_nested(config, self._key, self._channel_mask())


# ── Main widget ───────────────────────────────────────────────────────────────

class DeviceConfigWidget(QWidget):
    """Schema-driven configuration widget.

    Builds a form from a list of FieldDef descriptors.  On every field change
    the new value is written back to AppState.  ``configure_clicked`` is emitted
    when the user clicks 'Configure'.
    """

    configure_clicked = Signal()

    def __init__(
        self,
        schema: list[FieldDef],
        state: AppState,
        configure_label: str = "Configure",
        show_advanced: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._renderers: list = []

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)

        for fdef in schema:
            if not show_advanced and getattr(fdef, "advanced", False):
                continue
            renderer = self._make_renderer(fdef, form)
            if renderer is not None:
                self._renderers.append(renderer)

        form_widget = QWidget()
        form_widget.setLayout(form)
        outer.addWidget(form_widget)

        outer.addSpacing(6)
        self._configure_btn = QPushButton(configure_label)
        self._configure_btn.clicked.connect(self.configure_clicked)
        outer.addWidget(self._configure_btn)
        outer.addStretch()

        self.setLayout(outer)

        # Initial load from current state
        self.load_from_state()

        # React to external state changes
        state.settings_changed.connect(self._on_settings_changed)

    def _make_renderer(self, fdef: FieldDef, layout: QFormLayout):
        if isinstance(fdef, ComboField):
            return _ComboRenderer(fdef, self._state, layout, self._push_to_state)
        if isinstance(fdef, IntField):
            return _IntRenderer(fdef, self._state, layout, self._push_to_state)
        if isinstance(fdef, FloatField):
            return _FloatRenderer(fdef, self._state, layout, self._push_to_state)
        if isinstance(fdef, CheckField):
            return _CheckRenderer(fdef, self._state, layout, self._push_to_state)
        if isinstance(fdef, ChannelMaskField):
            return _ChannelMaskRenderer(fdef, self._state, layout, self._push_to_state)
        return None

    def load_from_state(self) -> None:
        """Populate all form fields from the current AppState config."""
        cfg = self._state.config
        for r in self._renderers:
            r.load(cfg)

    def _push_to_state(self) -> None:
        """Write all current field values to AppState (one update)."""
        partial: dict = {}
        for r in self._renderers:
            r.apply(partial)
        self._state.update(partial)

    def _on_settings_changed(self, config: dict) -> None:
        """Called when AppState emits settings_changed — reload without re-emitting."""
        for r in self._renderers:
            r.load(config)

    def set_capture_active(self, active: bool) -> None:
        """Disable mode-sensitive fields while a capture is running."""
        self._configure_btn.setEnabled(not active)


# ── Plot config base class ─────────────────────────────────────────────────────

class PlotConfigWidget(QWidget):
    """Schema-driven sidebar widget for plot mode / file browse / display mode.

    Accepts a list of PlotFieldDef descriptors (from plot_schemas.py) and builds
    the corresponding controls.  Subclasses call super().__init__(schema) then
    append device-specific sections to self._outer_layout.
    """

    plot_mode_changed    = Signal(str)   # "live" | "load" | "disabled" | …
    display_mode_changed = Signal(str)   # value string from DisplayModeField.options
    browse_clicked       = Signal()      # user clicked the Browse button

    def __init__(self, schema: "list", parent=None) -> None:
        super().__init__(parent)
        self._file_row: QWidget | None = None
        self._mode_btns: list[QRadioButton] = []
        self._mode_values: list[str] = []
        self._disp_sel: QComboBox | None = None
        self.loaded_file: QLineEdit | None = None
        self.browse_btn: QPushButton | None = None

        self._outer_layout = QVBoxLayout()
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(4)

        from pixels.gui.plot_schemas import PlotModeField, FileBrowseField, DisplayModeField
        for fdef in schema:
            if isinstance(fdef, PlotModeField):
                self._build_mode_row(fdef)
            elif isinstance(fdef, FileBrowseField):
                self._build_file_row(fdef)
            elif isinstance(fdef, DisplayModeField):
                self._build_display_row(fdef)

        self.setLayout(self._outer_layout)

    # ── Schema renderers ──────────────────────────────────────────────────────

    def _build_mode_row(self, f) -> None:
        _labels = {"live": "Live", "load": "Load File", "disabled": "Disabled"}
        row = QHBoxLayout()
        grp = QButtonGroup(self)
        for i, mode in enumerate(f.modes):
            btn = QRadioButton(_labels.get(mode, mode.title()))
            if i == 0:
                btn.setChecked(True)
            grp.addButton(btn)
            row.addWidget(btn)
            self._mode_btns.append(btn)
            self._mode_values.append(mode)
            btn.toggled.connect(lambda checked: self._on_mode_changed() if checked else None)
        row.addStretch()
        self._outer_layout.addLayout(row)

    def _build_file_row(self, f) -> None:
        if f.label:
            self._outer_layout.addWidget(QLabel(f.label))
        self._file_row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.loaded_file = QLineEdit()
        self.loaded_file.setReadOnly(True)
        self.loaded_file.setPlaceholderText(f.placeholder)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_clicked)
        layout.addWidget(self.loaded_file, 1)
        layout.addWidget(self.browse_btn)
        self._file_row.setLayout(layout)
        self._file_row.setVisible(False)
        self._outer_layout.addWidget(self._file_row)

    def _build_display_row(self, f) -> None:
        row = QHBoxLayout()
        row.addWidget(QLabel("View:"))
        self._disp_sel = QComboBox()
        for val, label in f.options:
            self._disp_sel.addItem(label, val)
        row.addWidget(self._disp_sel, 1)
        self._outer_layout.addLayout(row)
        self._disp_sel.currentIndexChanged.connect(self._on_disp_changed)

    # ── Internal slots ────────────────────────────────────────────────────────

    def _on_mode_changed(self) -> None:
        mode = self.current_mode
        if self._file_row is not None:
            self._file_row.setVisible(mode == "load")
        self.plot_mode_changed.emit(mode)

    def _on_disp_changed(self) -> None:
        if self._disp_sel is not None:
            val = self._disp_sel.currentData()
            if val is not None:
                self.display_mode_changed.emit(val)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_mode(self) -> str:
        for btn, mode in zip(self._mode_btns, self._mode_values):
            if btn.isChecked():
                return mode
        return self._mode_values[0] if self._mode_values else ""

    def set_plot_mode(self, mode: str) -> None:
        """Programmatically select a mode button without emitting plot_mode_changed."""
        for btn, m in zip(self._mode_btns, self._mode_values):
            if m == mode:
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
                self._on_mode_changed()
                return

    def set_display_options(
        self, options: list[tuple[str, str]], keep_current: bool = True
    ) -> None:
        """Replace combo options; preserves the current selection by value when possible."""
        if self._disp_sel is None:
            return
        current = self._disp_sel.currentData() if keep_current else None
        self._disp_sel.blockSignals(True)
        self._disp_sel.clear()
        for val, label in options:
            self._disp_sel.addItem(label, val)
        if current is not None:
            for i in range(self._disp_sel.count()):
                if self._disp_sel.itemData(i) == current:
                    self._disp_sel.setCurrentIndex(i)
                    break
        self._disp_sel.blockSignals(False)
        self._on_disp_changed()
