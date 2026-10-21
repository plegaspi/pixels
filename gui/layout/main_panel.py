from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QTabWidget, QWidget, QVBoxLayout, QLabel

from pixels.gui.layout.plot import DataTab
from pixels.gui.layout.ad4134.registers import ADX134RegistersTab
from pixels.gui.layout.ads1219.data import ADS1219PlotTab
from pixels.gui.layout.live_plot import LivePlotWidget
from pixels.gui.layout.spatial import SpatialTab


class ADX134Tab(QWidget):
    """Container that stacks DataTab (waveform) and SpatialTab inside one main-panel tab.

    The active view is controlled by set_view() — called from the ADX134 sidebar
    plot-config widget when the user switches between Waveform and Spatial views.
    view_changed is emitted after the switch so the sidebar can stay in sync.
    """

    view_changed = Signal(str)   # "waveform" | "spatial"

    def __init__(self):
        super().__init__()
        self.data_tab    = DataTab()
        self.spatial_tab = SpatialTab()
        self._stack = QStackedWidget()
        self._stack.addWidget(self.data_tab)     # index 0 — waveform
        self._stack.addWidget(self.spatial_tab)  # index 1 — spatial
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self.setLayout(layout)
        self._view = "waveform"

    def set_view(self, view: str) -> None:
        self._stack.setCurrentIndex(0 if view == "waveform" else 1)
        self._view = view
        self.view_changed.emit(view)

    # Convenience delegation so callers that previously reached through main_panel
    # to the inner DataTab still work without needing to know the nesting.
    def set_plot_mode(self, mode: str) -> None:
        self.data_tab.set_plot_mode(mode)

    def set_display_mode(self, mode: str) -> None:
        self.data_tab.set_display_mode(mode)

    def set_demod_mode(self, enabled: bool) -> None:
        self.data_tab.set_demod_mode(enabled)


class BoardRegistersTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Board Registers"))
        self.setLayout(layout)


class MainPanel(QTabWidget):
    device_tab_changed = Signal(str)  # device key or "other"

    def __init__(self):
        super().__init__()
        self.setTabPosition(QTabWidget.TabPosition.North)
        self.setMovable(True)

        self._device_tabs: dict[str, QWidget] = {}  # device_key -> primary tab widget
        self._device_all_tabs: dict[str, list] = {}  # device_key -> all associated tabs

        # Device-specific tabs
        self.adx134_tab = ADX134Tab()
        self.adc_tab = ADX134RegistersTab()
        self.ads_tab = ADS1219PlotTab()
        self.rfsoc_tab = LivePlotWidget()
        self.board_tab = BoardRegistersTab()

        # Convenience aliases so MainWindow methods that reference data_tab / spatial_tab
        # continue to work without modification.
        self.data_tab    = self.adx134_tab.data_tab
        self.spatial_tab = self.adx134_tab.spatial_tab

        self._register_device_tab(
            "adx134",
            self.adx134_tab,
            "ADX134",
            extra_tabs=[
                (self.adc_tab, "ADX134 Registers"),
            ],
        )
        self._register_device_tab("ads1219", self.ads_tab, "ADS1219")
        self._register_device_tab("rfsoc", self.rfsoc_tab, "RFSoC")
        # self.addTab(self.board_tab, "Board Registers")

        self.currentChanged.connect(self._on_tab_changed)

    # ── Registration ──────────────────────────────────────────────────────────

    def _register_device_tab(
        self, key: str, widget: QWidget, label: str, extra_tabs: list | None = None
    ) -> None:
        """Register a tab as belonging to a specific device."""
        self._device_tabs[key] = widget
        self.addTab(widget, label)
        all_tabs = [widget]
        for extra_widget, extra_label in extra_tabs or []:
            self.addTab(extra_widget, extra_label)
            all_tabs.append(extra_widget)
        self._device_all_tabs[key] = all_tabs

    def set_device_enabled(self, key: str, enabled: bool) -> None:
        """Show or hide all tabs associated with a device."""
        for widget in self._device_all_tabs.get(key, []):
            idx = self.indexOf(widget)
            if idx >= 0:
                self.setTabVisible(idx, enabled)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_tab_changed(self, idx: int) -> None:
        tab = self.widget(idx)
        for key, widget in self._device_tabs.items():
            if tab is widget:
                self.device_tab_changed.emit(key)
                return
        self.device_tab_changed.emit("other")
