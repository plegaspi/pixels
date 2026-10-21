import os
import sys
import time
import threading
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QDockWidget,
    QMessageBox,
    QLabel,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction

from pixels.gui.layout.sidebar import SidebarWidget
from pixels.gui.layout.toolbar import ControlToolbar
from pixels.gui.layout.main_panel import MainPanel
from pixels.gui.layout.bottom_panel import BottomPanel
from pixels.gui.state import AppState
from pixels.gui.device_registry import DEVICE_REGISTRY


class MainWindow(QMainWindow):
    _configure_done = Signal(bool, str, bool, bool)
    _ads1219_configure_done = Signal(bool, str)
    _tx_done = Signal(bool, str)
    _ads1219_ready = Signal(object)  # emits ADS1219 instance on connect
    _ads1219_done = Signal()
    _rfsoc_ready = Signal(object)  # emits RFSoCDAQ instance on start
    _rfsoc_done = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixels")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1280, available.width()), min(800, available.height()))

        self.setStyleSheet("""
            QDockWidget {
                border: 1px solid #888;
            }
            QDockWidget::title {
                background: #d0d0d0;
                padding: 4px 6px;
                font-weight: bold;
                border-bottom: 1px solid #888;
            }
        """)

        self.layout().setContentsMargins(8, 4, 8, 8)

        self.toolbar = ControlToolbar()
        self.toolbar.setObjectName("toolbar-control")
        for _desc in DEVICE_REGISTRY:
            self.toolbar.capture_en.register_device(
                _desc.key, _desc.label, default_checked=_desc.default_enabled
            )
        self.addToolBar(self.toolbar)
        self._device_enabled: dict[str, bool] = {
            _desc.key: _desc.default_enabled for _desc in DEVICE_REGISTRY
        }

        # Shared application state — single source of truth for all config
        self._state = AppState(self)
        self._state.load_defaults()

        self.main_panel = MainPanel()
        self.setCentralWidget(self.main_panel)

        self.sidebar = SidebarWidget(self._state)
        self._sidebar_dock = QDockWidget("Configuration", self)
        self._sidebar_dock.setObjectName("dock-configuration")
        self._sidebar_dock.setWidget(self.sidebar)
        self._sidebar_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._sidebar_dock)
        self.resizeDocks([self._sidebar_dock], [460], Qt.Orientation.Horizontal)

        self._bottom_panel = BottomPanel()
        self._bottom_dock = QDockWidget("Monitor", self)
        self._bottom_dock.setObjectName("dock-monitor")
        self._bottom_dock.setWidget(self._bottom_panel)
        self._bottom_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._bottom_dock)

        # Menu bar
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction(QAction("New", self))
        file_menu.addAction(QAction("Open…", self))
        file_menu.addSeparator()

        save_act = QAction("Save Settings…", self)
        save_act.triggered.connect(self._save_configs)
        file_menu.addAction(save_act)

        advanced_act = QAction("Advanced Settings…", self)
        advanced_act.setShortcut("Ctrl+,")
        advanced_act.triggered.connect(self.sidebar.open_advanced_settings)
        file_menu.addAction(advanced_act)

        file_menu.addSeparator()
        load_prev_action = QAction("Restore Last Session", self)
        load_prev_action.setShortcut("Ctrl+Shift+L")
        load_prev_action.triggered.connect(self.sidebar.load_previous_configs)
        file_menu.addAction(load_prev_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("View")
        self._fullscreen_action = QAction("Full Screen", self, checkable=True)
        self._fullscreen_action.setShortcut("F11")
        self._fullscreen_action.toggled.connect(
            lambda on: self.showMaximized() if on else self.showNormal()
        )
        view_menu.addAction(self._fullscreen_action)
        view_menu.addSeparator()

        self._config_action = QAction(
            "Configuration", self, checkable=True, checked=True
        )
        self._config_action.setShortcut("Ctrl+Shift+C")
        self._config_action.toggled.connect(self._sidebar_dock.setVisible)
        self._sidebar_dock.visibilityChanged.connect(self._config_action.setChecked)
        view_menu.addAction(self._config_action)

        self._monitor_action = QAction("Monitor", self, checkable=True, checked=False)
        self._monitor_action.setShortcut("Ctrl+M")
        self._monitor_action.toggled.connect(self._bottom_dock.setVisible)
        self._bottom_dock.visibilityChanged.connect(self._monitor_action.setChecked)
        self._bottom_dock.setVisible(False)
        view_menu.addAction(self._monitor_action)

        # Status bar — left: board state  |  right: live capture stats
        self._status_label = QLabel("Not configured")
        self._capture_label = QLabel("")
        self.statusBar().addWidget(self._status_label, 1)
        self.statusBar().addPermanentWidget(self._capture_label)

        self._was_configured = False
        self._adx134_configured = False
        self._configured_odr: int | None = None
        self._configured_demod: bool | None = None
        self._daq = None
        self._adc = None
        # Set when no ADS1219 configure is in progress; capture waits on this.
        self._ads1219_cfg_done = threading.Event()
        self._ads1219_cfg_done.set()

        # Device capture handler registry: key -> (start_fn, stop_fn)
        # Uses naming convention _start_{key} / _stop_{key}; AttributeError at
        # startup if a registered device is missing its handlers.
        self._device_capture_handlers: dict = {
            _desc.key: (
                getattr(self, f"_start_{_desc.key}"),
                getattr(self, f"_stop_{_desc.key}"),
            )
            for _desc in DEVICE_REGISTRY
        }
        self._ads1219_save_path: str | None = None
        self._capture_connections: list = []
        self._last_bin_dir: str = ""
        self._active_device: str = "adx134"

        self._ads1219_ready.connect(self._on_ads1219_ready)
        self._ads1219_done.connect(self._on_ads1219_done)
        self._rfsoc_ready.connect(self._on_rfsoc_ready)
        self._rfsoc_done.connect(self._on_rfsoc_done)
        self._rfsoc: object = None

        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(2000)
        self._capture_timer.timeout.connect(self._update_capture_status)

        # Device visibility is driven by AppState — apply current state and react to changes
        for _desc in DEVICE_REGISTRY:
            _enabled = bool(
                self._state.get("devices", _desc.key, "enabled", default=_desc.default_enabled)
            )
            self._on_device_enabled_changed(_desc.key, _enabled)

        self._state.device_enabled_changed.connect(self._on_device_enabled_changed)
        self._state.settings_changed.connect(self.main_panel.adc_tab.load_from_cfg)
        self._state.settings_changed.connect(self._on_adx134_settings_changed)
        self.main_panel.adc_tab.load_from_cfg(self._state.config)
        # Keep legacy signal wiring for widgets that haven't been migrated
        self.sidebar.data_cfg.device_enabled_changed.connect(
            self._on_device_enabled_changed
        )

        # Route stdout to Console tab based on debug flag (yaml default + live override)
        self.sidebar.data_cfg.debug_changed.connect(self._on_debug_changed)
        self.sidebar.data_cfg.demod_mode_changed.connect(
            self.sidebar.adx134_plot_cfg.set_demod_mode
        )
        self.sidebar.data_cfg.demod_mode_changed.connect(
            self.main_panel.data_tab.set_demod_mode
        )
        if self._state.get("app", "debug", default=False):
            self._on_debug_changed(True)

        # Wire hardware-wide and device-specific configure buttons.
        self.toolbar.configure_btn.clicked.connect(self._on_configure)
        self.sidebar._adx134_adc.configure_clicked.connect(self._on_configure_adx134)
        self.sidebar._ads1219_adc.configure_clicked.connect(self._on_configure_ads1219)
        self._ads1219_configure_done.connect(self._on_ads1219_configure_done)
        self._configure_done.connect(self._on_configure_done)

        # Wire Capture Data toggle
        self.toolbar.capture_en.toggled.connect(self._on_capture_toggle)
        self._tx_done.connect(self._on_tx_done)

        # Wire plot config widgets -> main panel tabs via registry
        for _desc in DEVICE_REGISTRY:
            _cfg = getattr(self.sidebar, f"{_desc.key}_plot_cfg", None)
            if _cfg is None:
                continue
            if _desc.tab_attr:
                _tab = getattr(self.main_panel, _desc.tab_attr, None)
                for _sig, _method in _desc.plot_cfg_wiring.items():
                    if _tab is not None:
                        getattr(_cfg, _sig).connect(getattr(_tab, _method))
            if _desc.wire_plot_cfg is not None:
                _desc.wire_plot_cfg(_cfg, self)

        # Contextual sidebar + toolbar: switch based on active tab
        self.main_panel.device_tab_changed.connect(self._on_device_changed)

    def _write_settings_yaml(self, capture_dir: str) -> None:
        """Write the full active in-memory config to capture_dir/settings.yaml."""
        import yaml

        try:
            cfg = dict(self.sidebar.data_cfg._cfg_data)
            cfg["save_dir"] = self.sidebar.data_cfg.save_dir.currentText().strip()

            adx134_conn: dict = {}
            for i, row in enumerate(self.sidebar.conn_cfg.ports):
                host = row.host_ip.text().strip()
                adx134_conn[f"eth{i}"] = {
                    "interface": row.iface_sel.currentText(),
                    "host_ip": host or "auto",
                    "remote_ip": row.remote_ip.text().strip(),
                    "port": row.remote_port.text().strip(),
                }
            conn_dict: dict = {"adx134": adx134_conn}
            ads_cfg = self.sidebar.conn_cfg.get_ads1219_config()
            if ads_cfg:
                conn_dict["ads1219"] = ads_cfg
            cfg["connections"] = conn_dict

            os.makedirs(capture_dir, exist_ok=True)
            with open(os.path.join(capture_dir, "settings.yaml"), "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        except Exception:
            pass  # non-critical

    def _on_device_enabled_changed(self, key: str, enabled: bool) -> None:
        self._device_enabled[key] = enabled
        self.main_panel.set_device_enabled(key, enabled)
        self.toolbar.capture_en.set_device_enabled(key, enabled)
        # Sync back to AppState (no-op if already set there — avoids signal loops)
        self._state.config.setdefault("devices", {}).setdefault(key, {})[
            "enabled"
        ] = enabled

    def _on_device_changed(self, device: str) -> None:
        self._active_device = device
        self.sidebar.set_active_device(device)

    def _run_ads1219_toggle(self, enabled: bool) -> None:
        if enabled:
            # Wait for any in-progress configure to finish (avoids opening the
            # serial port twice or racing against init/set_channels commands).
            self._ads1219_cfg_done.wait(timeout=15.0)
            conn = self.sidebar.conn_cfg.get_ads1219_config()
            if not conn:
                self._tx_done.emit(
                    False, "ADS1219 not configured — set port in Connection Config"
                )
                return
            try:
                save_path = getattr(self, "_ads1219_save_path", None)

                if self._adc is not None and self._adc.is_connected:
                    self._adc.start(save_path=save_path)
                else:
                    from pixels.daq.ads1219 import ADS1219, ADS1219Config

                    ads_data = self.sidebar.data_cfg._cfg_data.get("ads1219", {})
                    config = ADS1219Config(
                        gain=int(ads_data.get("gain", 0)),
                        data_rate=int(ads_data.get("data_rate", 3)),
                    )
                    adc = ADS1219(conn["port"], conn["baud"], config=config)
                    adc.connect()
                    adc.init()
                    mask = int(ads_data.get("channel_mask", 0x03))
                    adc.set_channels(mask)
                    adc.start(save_path=save_path)
                    self._adc = adc

                self._ads1219_ready.emit(self._adc)
            except Exception as exc:
                self._tx_done.emit(False, f"ADS1219 connect failed: {exc}")
        else:
            if self._adc:
                try:
                    self._adc.stop()
                except Exception as exc:
                    self._tx_done.emit(False, f"ADS1219 stop/save failed: {exc}")
                try:
                    self._adc.disconnect()
                except Exception:
                    pass
                self._adc = None
            self._ads1219_done.emit()

    def _on_ads1219_ready(self, adc) -> None:
        self.sidebar.ads1219_plot_cfg.set_plot_mode("live")
        self.main_panel.ads_tab.start(adc)

        mask = int(
            self.sidebar.data_cfg._cfg_data.get("ads1219", {}).get("channel_mask", 0x7F)
        )
        active = [m for m in range(7) if mask & (1 << m)]
        self.sidebar.ads1219_plot_cfg.update_available_channels(active)
        self._status_label.setText("ADS1219 streaming")

    def _on_ads1219_done(self) -> None:
        self.main_panel.ads_tab.stop()
        save_path = self._ads1219_save_path
        if save_path and os.path.exists(save_path):
            plot_cfg = self.sidebar.ads1219_plot_cfg
            plot_cfg.loaded_file.setText(save_path.split("/")[-1])
            plot_cfg.loaded_file.setToolTip(save_path)
            plot_cfg.set_plot_mode("load")
            self.main_panel.ads_tab.load_file(save_path)
        self._status_label.setText("ADS1219 stopped")
        if self._daq is None:
            self.toolbar.capture_en.setEnabled(True)

    def _on_configure(self):
        adx_enabled = self._device_enabled.get("adx134", False)
        ads_enabled = self._device_enabled.get("ads1219", False)
        if not adx_enabled and not ads_enabled:
            self.toolbar.mark_configured(self._adx134_configured)
            QMessageBox.warning(
                self,
                "No Devices Enabled",
                "Enable at least one device before configuring hardware.",
            )
            return

        connections = self.sidebar.conn_cfg.get_connections() if adx_enabled else []
        ads_conn = self.sidebar.conn_cfg.get_ads1219_config() if ads_enabled else None
        self._was_configured = self._adx134_configured
        threading.Thread(
            target=self._run_configure_enabled,
            args=(adx_enabled, connections, ads_enabled, ads_conn),
            daemon=True,
        ).start()

    def _on_configure_adx134(self):
        connections = self.sidebar.conn_cfg.get_connections()
        if not connections:
            QMessageBox.warning(
                self,
                "No ADX134 Connections",
                "Set ADX134 UDP connections before configuring ADX134.",
            )
            return
        self._was_configured = self._adx134_configured
        threading.Thread(
            target=self._run_configure_adx134,
            args=(connections,),
            daemon=True,
        ).start()

    def _configure_adx134_sync(self, connections: list) -> tuple[bool, str]:
        from pixels.control.board import tx_enable, set_odr_config, set_demod_mode
        from pixels.daq.adx134.profiles import profile_from_config

        odr_idx = self.sidebar.data_cfg.odr_index
        profile = profile_from_config(self.sidebar.data_cfg._cfg_data)
        demod = profile.demod_mode
        try:
            for conn in connections:
                tx_enable(False, conn)
                set_odr_config(odr_idx, conn)
                set_demod_mode(demod, conn)
            mode_str = "demod I/Q" if demod else "raw"
            self._connections = connections
            self._capture_connections = connections
            return True, f"ADX134 configured (ODR {odr_idx}, {mode_str})"
        except Exception as exc:
            return False, f"ADX134 failed: {exc}"

    def _run_configure_adx134(self, connections: list) -> None:
        success, message = self._configure_adx134_sync(connections)
        if success:
            message = f"{message}; ADC clock starts on capture/tx-only"
        self._configure_done.emit(success, message, success, False)

    def _run_configure_enabled(
        self,
        adx_enabled: bool,
        adx_connections: list,
        ads_enabled: bool,
        ads_conn: dict | None,
    ) -> None:
        messages: list[str] = []
        any_configured = False
        any_failed = False
        adx_configured = self._adx134_configured
        ads_configured = False

        if adx_enabled:
            if adx_connections:
                ok, msg = self._configure_adx134_sync(adx_connections)
                any_configured = any_configured or ok
                any_failed = any_failed or not ok
                adx_configured = ok
                messages.append(msg)
            else:
                adx_configured = False
                messages.append("ADX134 skipped: no valid UDP connections")

        if ads_enabled:
            if ads_conn:
                ok, msg = self._configure_ads1219_sync(ads_conn)
                any_configured = any_configured or ok
                any_failed = any_failed or not ok
                ads_configured = ok
                messages.append(msg)
            else:
                messages.append("ADS1219 skipped: no serial port")

        success = any_configured and not any_failed
        if not messages:
            messages.append("No enabled devices were configured")
        self._configure_done.emit(
            success, "; ".join(messages), adx_configured, ads_configured
        )

    def _on_adx134_settings_changed(self, config: dict) -> None:
        if not self._adx134_configured:
            return
        from pixels.daq.adx134.profiles import profile_from_config
        new_odr = int(config.get("adx134", {}).get("clock_gen", {}).get("odr_config", 0))
        new_demod = profile_from_config(config).demod_mode
        if new_odr != self._configured_odr or new_demod != self._configured_demod:
            self._adx134_configured = False
            self.toolbar.mark_configured(False)

    def _on_configure_done(
        self, success: bool, message: str, adx_configured: bool, ads_configured: bool
    ) -> None:
        self._adx134_configured = adx_configured
        if adx_configured:
            from pixels.daq.adx134.profiles import profile_from_config
            self._configured_odr = int(
                self._state.get("adx134", "clock_gen", "odr_config", default=0)
            )
            self._configured_demod = profile_from_config(self._state.config).demod_mode
        self.toolbar.mark_configured(adx_configured)
        self._status_label.setText(message)
        if adx_configured:
            conns = getattr(self, "_connections", [])
            if conns:
                self.main_panel.adc_tab.set_connection(conns[0])
        if ads_configured:
            self._sync_ads1219_channels()
        if success and self._was_configured:
            QMessageBox.information(self, "Hardware Reconfigured", message)

    def _on_configure_ads1219(self, warn_if_missing: bool = True) -> None:
        conn = self.sidebar.conn_cfg.get_ads1219_config()
        if not conn:
            if warn_if_missing:
                QMessageBox.warning(
                    self, "Not Configured", "Set ADS1219 port in Connection Config."
                )
            return
        threading.Thread(
            target=self._run_configure_ads1219, args=(conn,), daemon=True
        ).start()

    def _configure_ads1219_sync(self, conn: dict) -> tuple[bool, str]:
        from pixels.daq.ads1219 import ADS1219, ADS1219Config
        from pixels.daq.ads1219.constants import DR_LABELS, MUX_LABELS

        ads = self.sidebar.data_cfg._cfg_data.get("ads1219", {})
        mask = int(ads.get("channel_mask", 0x03))
        config = ADS1219Config(
            gain=int(ads.get("gain", 0)),
            data_rate=int(ads.get("data_rate", 3)),
        )
        gain_label = "4×" if config.gain else "1×"
        dr_label = DR_LABELS.get(config.data_rate, str(config.data_rate))
        ch_labels = [MUX_LABELS.get(m, str(m)) for m in range(7) if mask & (1 << m)]
        applied_msg = (
            f"ADS1219 configured — "
            f"gain={gain_label}  "
            f"DR={dr_label}  "
            f"channels: {', '.join(ch_labels) or 'none'}"
        )
        try:
            if self._adc is not None and self._adc.is_connected:
                self._adc._config = config
                self._adc.init()
                self._adc.set_channels(mask)
            else:
                adc = ADS1219(conn["port"], conn["baud"], config=config)
                adc.connect()
                adc.init()
                adc.set_channels(mask)
                self._adc = adc

            return True, applied_msg
        except Exception as exc:
            return False, f"ADS1219 configure failed: {exc}"

    def _run_configure_ads1219(self, conn: dict) -> None:
        self._ads1219_cfg_done.clear()
        try:
            success, message = self._configure_ads1219_sync(conn)
        finally:
            self._ads1219_cfg_done.set()
        self._ads1219_configure_done.emit(success, message)

    def _sync_ads1219_channels(self) -> None:
        mask = int(
            self.sidebar.data_cfg._cfg_data.get("ads1219", {}).get("channel_mask", 0x7F)
        )
        active = [m for m in range(7) if mask & (1 << m)]
        self.sidebar.ads1219_plot_cfg.update_available_channels(active)
        self.main_panel.ads_tab.set_visible_channels(active)

    def _on_ads1219_configure_done(self, success: bool, message: str) -> None:
        self._status_label.setText(message)
        if not success:
            QMessageBox.warning(self, "ADS1219 Configure Failed", message)
            return

        self._sync_ads1219_channels()

        if self._adc is not None:
            mask = int(
                self.sidebar.data_cfg._cfg_data.get("ads1219", {}).get(
                    "channel_mask", 0x7F
                )
            )
            self._adc.set_channels(mask)
            self._adc.clear()

    def _revert_capture_toggle(self) -> None:
        btn = self.toolbar.capture_en
        btn.blockSignals(True)
        btn.setChecked(not btn.isChecked())
        btn._on_toggle(btn.isChecked())
        btn.blockSignals(False)

    def _on_capture_toggle(self, enabled: bool) -> None:
        btn = self.toolbar.capture_en

        if btn.mode == "tx_only":
            if not self._adx134_configured:
                self._revert_capture_toggle()
                QMessageBox.warning(
                    self,
                    "ADX134 Not Configured",
                    "Configure ADX134 before enabling TX.",
                )
                return
            btn.setEnabled(False)
            if enabled:
                threading.Thread(target=self._run_tx_only_start, daemon=True).start()
            else:
                threading.Thread(target=self._run_tx_only_stop, daemon=True).start()
            return

        active = btn.enabled_devices
        if not active:
            self._revert_capture_toggle()
            QMessageBox.warning(
                self,
                "Nothing Selected",
                "Select at least one device in the Capture dropdown.",
            )
            return

        save_dir = self.sidebar.data_cfg.save_dir.currentText().strip()
        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

        if enabled and save_dir:
            self._write_settings_yaml(os.path.join(save_dir, ts))

        self._capture_ts = ts
        self._capture_save_dir = save_dir

        for key in active:
            start_fn, stop_fn = self._device_capture_handlers.get(key, (None, None))
            if start_fn is None:
                continue
            if enabled:
                ok = start_fn(save_dir, ts)
                if not ok:
                    return
            else:
                stop_fn()

    # ── Per-device capture handlers ───────────────────────────────────────────

    def _start_adx134(self, save_dir: str, ts: str) -> bool:
        if not self._adx134_configured:
            self._revert_capture_toggle()
            QMessageBox.warning(
                self,
                "ADX134 Not Configured",
                "Configure ADX134 before capturing ADX134.",
            )
            return False
        connections = self.sidebar.conn_cfg.get_connections()
        if not connections:
            self._revert_capture_toggle()
            QMessageBox.warning(
                self, "No Connections", "Set ADX134 UDP connections before capturing."
            )
            return False
        if not save_dir:
            self._revert_capture_toggle()
            QMessageBox.warning(
                self,
                "No Save Directory",
                "Set a Save Directory in Capture Config before capturing.",
            )
            return False
        self._capture_connections = connections
        bin_dir = os.path.join(save_dir, ts, "bins")
        os.makedirs(bin_dir, exist_ok=True)
        self._daq = self._build_daq(connections, bin_dir)
        self.toolbar.capture_en.setEnabled(False)
        threading.Thread(target=self._run_capture_start, daemon=True).start()
        return True

    def _stop_adx134(self) -> None:
        self.toolbar.capture_en.setEnabled(False)
        if self._daq is not None:
            threading.Thread(target=self._run_capture_stop, daemon=True).start()

    def _start_ads1219(self, save_dir: str, ts: str) -> bool:
        if save_dir:
            capture_dir = os.path.join(save_dir, ts)
            os.makedirs(capture_dir, exist_ok=True)
            self._ads1219_save_path = os.path.join(capture_dir, "ads1219.h5")
        else:
            self._ads1219_save_path = None
        threading.Thread(
            target=self._run_ads1219_toggle, args=(True,), daemon=True
        ).start()
        return True

    def _stop_ads1219(self) -> None:
        self.toolbar.capture_en.setEnabled(False)
        threading.Thread(
            target=self._run_ads1219_toggle, args=(False,), daemon=True
        ).start()

    def _start_rfsoc(self, save_dir: str, ts: str) -> bool:
        cfg_dict = self.sidebar.conn_cfg.get_rfsoc_config()

        class _Cfg:
            bind = cfg_dict["bind"]
            port = cfg_dict["port"]
            mode = cfg_dict["mode"]

        cfg = _Cfg()
        if save_dir:
            capture_dir = os.path.join(save_dir, ts)
            os.makedirs(capture_dir, exist_ok=True)
            save_path = os.path.join(capture_dir, "rfsoc.h5")
        else:
            save_path = None
        threading.Thread(
            target=self._run_rfsoc_start,
            args=(cfg.bind, cfg.port, cfg.mode, save_path),
            daemon=True,
        ).start()
        return True

    def _stop_rfsoc(self) -> None:
        threading.Thread(target=self._run_rfsoc_stop, daemon=True).start()

    def _run_rfsoc_start(self, bind: str, port: int, mode: str, save_path) -> None:
        from pixels.daq.rfsoc import RFSoCDAQ

        try:
            daq = RFSoCDAQ(bind=bind, port=port, mode=mode)
            daq.start(save_path=save_path)
            self._rfsoc = daq
            self._rfsoc_ready.emit(daq)
        except Exception as exc:
            self._tx_done.emit(False, f"RFSoC start failed: {exc}")

    def _run_rfsoc_stop(self) -> None:
        daq = self._rfsoc
        if daq is not None:
            try:
                daq.stop()
            except Exception:
                pass
            self._rfsoc = None
        self._rfsoc_done.emit()

    def _on_rfsoc_ready(self, daq) -> None:
        self.main_panel.rfsoc_tab.start(daq)
        self._status_label.setText(
            f"RFSoC: listening {daq._mode.upper()} on {daq._bind}:{daq._port}"
        )
        self.toolbar.capture_en.setEnabled(True)

    def _on_rfsoc_done(self) -> None:
        self.main_panel.rfsoc_tab.stop()
        self._status_label.setText("RFSoC stopped")
        self.toolbar.capture_en.setEnabled(True)

    def _browse_and_load_rfsoc(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select RFSoC HDF5 File",
            "",
            "HDF5 files (*.hdf5 *.h5);;All files (*)",
        )
        if not path:
            return
        from pixels.models import RFSoCData
        from pathlib import Path

        plot_cfg = self.sidebar.rfsoc_plot_cfg
        plot_cfg.loaded_file.setText(Path(path).name)
        plot_cfg.loaded_file.setToolTip(path)
        # Load into the tab via a temporary RFSoCData reader
        try:
            with RFSoCData(Path(path)) as d:
                import numpy as np
                from pixels.daq._base import SampleBatch

                data = d.read_samples(0, d.n_samples).astype(np.float64)
                t = (
                    np.arange(d.n_samples, dtype=np.float64) / d.sample_rate
                    if d.sample_rate > 0
                    else np.arange(d.n_samples, dtype=np.float64)
                )
                batch = SampleBatch(
                    data=data,
                    time=t,
                    channel_labels=d.channel_labels,
                    units="counts",
                    sample_rate=d.sample_rate,
                )
            tab = self.main_panel.rfsoc_tab
            tab._plot.clear()
            tab._curves.clear()
            for i, label in enumerate(batch.channel_labels):
                curve = tab._get_or_create_curve(label, i)
                curve.setData(batch.time, batch.data[:, i])
            tab._status_label.setText(
                f"{batch.n_samples:,} samples  ×  {batch.n_channels} ch  (file)"
            )
        except Exception as exc:
            plot_cfg.loaded_file.setText(f"Load failed: {exc}")

    def _build_daq(self, connections, bin_dir: str):
        from pixels.daq.adx134 import ADX134DAQ
        from pixels.daq.adx134.profiles import profile_from_config

        cfg_data = self.sidebar.data_cfg._cfg_data
        adx134_cfg = cfg_data.get("adx134", {})
        cap_cfg = adx134_cfg.get("capture", {})
        profile = profile_from_config(cfg_data)
        debug = bool(cfg_data.get("app", {}).get("debug", False))
        ports_data = adx134_cfg.get("ports", {})
        port_map = []
        for i, conn in enumerate(connections):
            dat = ports_data.get(f"eth{i}", {})
            port_map.append(
                {
                    "remote_ip": conn.fpga_ip,
                    "host_ip": conn.host_ip,
                    "port": conn.fpga_port,
                }
            )
        return ADX134DAQ(
            port_map=port_map,
            bin_dir=bin_dir,
            debug=debug,
            scratch_format=cap_cfg.get("scratch_format", "blosc_zstd"),
            write_batch=int(cap_cfg.get("write_batch", 512)),
            profile=profile,
        )

    def _run_capture_start(self) -> None:
        from pixels.control.board import tx_enable

        assert self._daq is not None
        try:
            self._daq.start(use_processes=True)
            if not self._daq.wait_ready(timeout=10.0):
                self._daq.stop()
                self._daq = None
                self._tx_done.emit(
                    False, "Capture start failed: receivers did not bind in time"
                )
                return
            for conn in self._capture_connections:
                tx_enable(True, conn)
            self._tx_done.emit(True, f"Capturing -> {self._daq.bin_dir}")
        except Exception as exc:
            try:
                self._daq.stop()
            except Exception:
                pass
            self._daq = None
            self._tx_done.emit(False, f"Capture start failed: {exc}")

    def _run_capture_stop(self) -> None:
        from pixels.control.board import tx_enable

        assert self._daq is not None
        bin_dir = self._daq.bin_dir
        try:
            for conn in self._capture_connections:
                tx_enable(False, conn)
            self._daq.stop()
        except Exception as exc:
            self._tx_done.emit(False, f"Capture stop failed: {exc}")
            return
        finally:
            self._daq = None
        self._last_bin_dir = bin_dir
        self._tx_done.emit(True, "Capture stopped")

    def _run_tx_only_start(self) -> None:
        from pixels.control.board import tx_enable

        try:
            for conn in self._capture_connections:
                tx_enable(True, conn)
            self._tx_done.emit(True, "TX enabled (no capture)")
        except Exception as exc:
            self._tx_done.emit(False, f"TX enable failed: {exc}")

    def _run_tx_only_stop(self) -> None:
        from pixels.control.board import tx_enable

        try:
            for conn in self._capture_connections:
                tx_enable(False, conn)
            self._tx_done.emit(True, "TX disabled")
        except Exception as exc:
            self._tx_done.emit(False, f"TX disable failed: {exc}")

    def _on_debug_changed(self, enabled: bool) -> None:
        import logging

        console = self._bottom_panel.console
        if enabled:
            console.install_logging(level=logging.DEBUG)
            self._bottom_dock.setVisible(True)
        else:
            console.uninstall_logging()
            self._bottom_dock.setVisible(False)

    def _update_capture_status(self) -> None:
        if self._daq is None:
            self._capture_timer.stop()
            self._capture_label.setText("")
            return
        from pixels.daq.adx134.packet_store import PacketReader

        elapsed = time.time() - self._daq._start_time
        parts = []
        for i in range(self._daq.n_ports):
            p = self._daq.bin_path(i)
            pkts = 0
            if os.path.exists(p):
                try:
                    with PacketReader(
                        p, swmr=p.endswith((".h5", ".hdf5")), profile=self._daq.profile
                    ) as reader:
                        reader.refresh()
                        pkts = reader.n_packets
                except (OSError, KeyError, ValueError):
                    pkts = 0
            parts.append(f"p{i}:{pkts:,}pkts")
        self._capture_label.setText(f"[{elapsed:6.0f}s]  {' | '.join(parts)}")

    def _on_tx_done(self, success: bool, message: str) -> None:
        self.toolbar.capture_en.setEnabled(True)
        tx_running = success and self.toolbar.capture_en.isChecked()
        self.main_panel.adc_tab.set_tx_active(tx_running)
        self.sidebar._adx134_adc.set_capture_active(tx_running)
        if not success:
            self._capture_timer.stop()
            self._status_label.setText(message)
            self._revert_capture_toggle()
        elif self.toolbar.capture_en.isChecked():
            self._status_label.setText(message)
            if self._daq is not None:
                # full capture just started — switch back to live view
                self._capture_timer.start()
                plot_cfg = self.sidebar.adx134_plot_cfg
                plot_cfg.live_plot.setChecked(True)
                plot_cfg.build_port_buttons(
                    self._daq.n_ports, self._daq.profile.n_chan_per_port
                )
                data_tab = self.main_panel.data_tab
                data_tab.update_channels(plot_cfg.channels)
                data_tab.start(self._daq, mode=plot_cfg.current_mode)
                self.main_panel.spatial_tab.start(self._daq)
        else:
            self._status_label.setText(message)
            if (
                self._daq is None
                and "adx134" in self.toolbar.capture_en.enabled_devices
            ):
                # full capture just stopped
                self._capture_timer.stop()
                self._capture_label.setText("")
                self.main_panel.data_tab.stop()
                self.main_panel.spatial_tab.stop()
                if self._last_bin_dir and os.path.exists(self._last_bin_dir):
                    self._auto_load_adx134(self._last_bin_dir)

    def _auto_load_adx134(self, bins_dir: str) -> None:
        from pixels.models import ADX134Data
        from pathlib import Path

        try:
            d = ADX134Data(Path(bins_dir))
            if d.n_ports == 0:
                return
            n_ports, n_chan_per_port, demod_mode = (
                d.n_ports,
                d.n_chan_per_port,
                d.demod_mode,
            )
            empty_ports = [i for i in range(n_ports) if d.n_packets(i) == 0]
            d.close()
        except Exception:
            return
        if empty_ports:
            self._status_label.setText(
                "Capture complete — no packets received (check connections and board configuration)"
            )
            return
        plot_cfg = self.sidebar.adx134_plot_cfg
        plot_cfg.set_demod_mode(demod_mode)
        plot_cfg.loaded_file.setText(Path(bins_dir).name)
        plot_cfg.loaded_file.setToolTip(bins_dir)
        plot_cfg.build_port_buttons(n_ports, n_chan_per_port)
        plot_cfg.load_data.setChecked(True)  # switches DataTab to load mode via signal
        self.main_panel.data_tab.load_file(
            bins_dir, self.sidebar.adx134_plot_cfg.channels
        )
        self.main_panel.spatial_tab.load_file(bins_dir)

    def _browse_and_load(self):
        from PySide6.QtWidgets import QFileDialog

        bins_dir = QFileDialog.getExistingDirectory(
            self, "Select Capture bins/ Directory", ""
        )
        if not bins_dir:
            return
        from pixels.models import ADX134Data
        from pathlib import Path

        try:
            d = ADX134Data(Path(bins_dir))
            n_ports = d.n_ports
            n_chan_per_port = d.n_chan_per_port
            demod_mode = d.demod_mode
            d.close()
        except Exception:
            return
        plot_cfg = self.sidebar.adx134_plot_cfg
        plot_cfg.set_demod_mode(demod_mode)
        plot_cfg.loaded_file.setText(Path(bins_dir).name)
        plot_cfg.loaded_file.setToolTip(bins_dir)
        plot_cfg.build_port_buttons(n_ports, n_chan_per_port)
        self.main_panel.data_tab.load_file(
            bins_dir, self.sidebar.adx134_plot_cfg.channels
        )
        self.main_panel.spatial_tab.load_file(bins_dir)

    def _browse_and_load_ads1219(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ADS1219 HDF5 File",
            "",
            "HDF5 files (*.hdf5 *.h5);;All files (*)",
        )
        if not path:
            return
        plot_cfg = self.sidebar.ads1219_plot_cfg
        plot_cfg.loaded_file.setText(path.split("/")[-1])
        plot_cfg.loaded_file.setToolTip(path)
        self.main_panel.ads_tab.load_file(path)

    def _save_configs(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        import yaml

        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if not folder:
            return

        try:
            cfg = dict(self.sidebar.data_cfg._cfg_data)
            cfg["save_dir"] = self.sidebar.data_cfg.save_dir.currentText().strip()

            adx134_conn: dict = {}
            for i, row in enumerate(self.sidebar.conn_cfg.ports):
                host = row.host_ip.text().strip()
                adx134_conn[f"eth{i}"] = {
                    "interface": row.iface_sel.currentText(),
                    "host_ip": host or "auto",
                    "remote_ip": row.remote_ip.text().strip(),
                    "port": row.remote_port.text().strip(),
                }
            conn_dict: dict = {"adx134": adx134_conn}
            ads_cfg = self.sidebar.conn_cfg.get_ads1219_config()
            if ads_cfg:
                conn_dict["ads1219"] = ads_cfg
            cfg["connections"] = conn_dict

            path = os.path.join(folder, "settings.yaml")
            with open(path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
            QMessageBox.information(self, "Saved", f"Saved: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", str(exc))

    def showEvent(self, event):
        super().showEvent(event)
        from PySide6.QtCore import QSettings, QByteArray

        s = QSettings("pixels", "pixels-gui")
        geom = s.value("window/geometry")
        state = s.value("window/state")
        if geom:
            self.restoreGeometry(geom)
        if state:
            self.restoreState(state)

    def closeEvent(self, event):
        from PySide6.QtCore import QSettings

        s = QSettings("pixels", "pixels-gui")
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        super().closeEvent(event)


def main_gui():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()
