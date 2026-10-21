from PySide6.QtWidgets import QToolBar, QPushButton, QWidget, QSizePolicy, QToolButton, QMenu
from PySide6.QtGui import QAction

_STYLE_ON  = ("QToolButton { background-color: #2e7d32; color: white; "
              "border: 1px solid #1b5e20; border-radius: 3px; padding: 3px 8px; }"
              "QToolButton:hover { background-color: #388e3c; }"
              "QToolButton::menu-button { border-left: 1px solid #1b5e20; width: 16px; }")
_STYLE_OFF = ("QToolButton { background-color: #b71c1c; color: white; "
              "border: 1px solid #7f0000; border-radius: 3px; padding: 3px 8px; }"
              "QToolButton:hover { background-color: #c62828; }"
              "QToolButton::menu-button { border-left: 1px solid #7f0000; width: 16px; }")


class ToggleButton(QPushButton):
    def __init__(self, label: str):
        super().__init__(label)
        self._base_label = label
        self.setCheckable(True)
        self.setFixedWidth(200)
        self.toggled.connect(self._on_toggle)
        self._on_toggle(False)

    def _on_toggle(self, checked: bool):
        if checked:
            self.setText(f"{self._base_label}: ON")
            self.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; "
                "border: 1px solid #1b5e20; border-radius: 3px; padding: 3px 8px; }"
                "QPushButton:hover { background-color: #388e3c; }"
            )
        else:
            self.setText(f"{self._base_label}: OFF")
            self.setStyleSheet(
                "QPushButton { background-color: #b71c1c; color: white; "
                "border: 1px solid #7f0000; border-radius: 3px; padding: 3px 8px; }"
                "QPushButton:hover { background-color: #c62828; }"
            )


class CaptureSplitButton(QToolButton):
    """
    Toggle button with a dropdown for device selection.

    Devices are registered via `register_device(key, label)` and are independently
    checkable. TX Enable Only is a separate exclusive mode.

    Use `enabled_devices` to get the set of currently-checked device keys.
    """

    def __init__(self):
        super().__init__()
        self._tx_only = False
        self._device_actions: dict[str, QAction] = {}
        self.setCheckable(True)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setFixedWidth(210)

        self._menu = QMenu(self)
        self._separator = self._menu.addSeparator()

        self._act_tx = QAction("TX Enable Only", self, checkable=True)
        self._act_tx.toggled.connect(self._on_tx_toggled)
        self._menu.addAction(self._act_tx)

        self.setMenu(self._menu)
        self.toggled.connect(self._on_toggle)
        self._on_toggle(False)

    # ── Device registration ───────────────────────────────────────────────────

    def register_device(self, key: str, label: str, default_checked: bool = True) -> None:
        """Register a capturable device. Call before the button is shown."""
        act = QAction(label, self, checkable=True)
        act.setChecked(default_checked)
        act.toggled.connect(self._on_device_toggled)
        self._device_actions[key] = act
        self._menu.insertAction(self._separator, act)

    # ── Properties ────────────────────────────────────────────────────────────

    def set_device_enabled(self, key: str, enabled: bool) -> None:
        """Show/hide a device's capture action; uncheck if disabling."""
        act = self._device_actions.get(key)
        if act:
            act.setVisible(enabled)
            if not enabled:
                act.blockSignals(True)
                act.setChecked(False)
                act.blockSignals(False)

    @property
    def enabled_devices(self) -> set[str]:
        """Set of device keys whose capture action is checked (empty if TX-only)."""
        if self._tx_only:
            return set()
        return {k for k, a in self._device_actions.items() if a.isChecked()}

    @property
    def mode(self) -> str:
        return "tx_only" if self._tx_only else "capture"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_device_toggled(self) -> None:
        if self.isChecked():
            return
        if self._tx_only:
            self._tx_only = False
            self._act_tx.blockSignals(True)
            self._act_tx.setChecked(False)
            self._act_tx.blockSignals(False)
        self._on_toggle(False)

    def _on_tx_toggled(self, checked: bool) -> None:
        if self.isChecked():
            self._act_tx.blockSignals(True)
            self._act_tx.setChecked(not checked)
            self._act_tx.blockSignals(False)
            return
        self._tx_only = checked
        self._on_toggle(False)

    def _on_toggle(self, checked: bool) -> None:
        label = "TX Enable Only" if self._tx_only else "Capture"
        self.setText(f"{label}: {'ON' if checked else 'OFF'}")
        self.setStyleSheet(_STYLE_ON if checked else _STYLE_OFF)


class ControlToolbar(QToolBar):
    _CONFIGURE_LABEL = "Configure All"

    def mark_configured(self, configured: bool):
        self._configured = configured
        self._set_configure_style()

    def _on_configure_clicked(self):
        self.configure_btn.setText("Configuring…")
        self.configure_btn.setStyleSheet(
            "QPushButton { background-color: #f57f17; color: white; "
            "border: none; border-radius: 3px; padding: 3px 12px; }"
        )
        self.configure_btn.setEnabled(False)

    def _set_configure_style(self):
        self.configure_btn.setText(self._CONFIGURE_LABEL)
        if self._configured:
            self.configure_btn.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; "
                "border: none; border-radius: 3px; padding: 3px 12px; }"
                "QPushButton:hover { background-color: #388e3c; }"
            )
        else:
            self.configure_btn.setStyleSheet(
                "QPushButton { padding: 3px 12px; border: 1px solid #555; border-radius: 3px; }"
                "QPushButton:hover { background-color: #e0e0e0; }"
            )
        self.configure_btn.setEnabled(True)

    def __init__(self):
        super().__init__("Control")
        self.setMovable(False)

        self.capture_en = CaptureSplitButton()

        self.configure_btn = QPushButton(self._CONFIGURE_LABEL)
        self._configured = False
        self._set_configure_style()
        self.configure_btn.clicked.connect(self._on_configure_clicked)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        self.addWidget(self.configure_btn)
        self.addSeparator()
        self.addWidget(self.capture_en)

        right_pad = QWidget()
        right_pad.setFixedWidth(8)
        self.addWidget(right_pad)
