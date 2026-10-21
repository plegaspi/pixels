"""Central registry for all DAQ device descriptors.

Each DeviceDescriptor captures everything the GUI needs to know about a device:
toolbar label/default, ADC config schema, ADC config widget factory (for custom
widgets), and plot config widget factory.  Adding a new device means adding one
entry here rather than wiring it in four separate places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from pixels.gui.device_schemas import FieldDef
    from pixels.gui.state import AppState
    from PySide6.QtWidgets import QWidget


@dataclass
class DeviceDescriptor:
    key: str
    label: str
    default_enabled: bool = True
    schema: list["FieldDef"] = field(default_factory=list)
    # Supply a factory only when the ADC config widget is not schema-driven
    # (e.g. RFSoCSettingsWidget).  None -> DeviceConfigWidget(schema, ...) is used.
    config_widget_factory: Callable[["AppState"], "QWidget"] | None = None
    configure_label: str = ""  # if empty, defaults to f"Configure {label}"
    # Supply a factory for the sidebar plot config panel.
    # None -> device has no plot config panel and is excluded from ContextualPlotCfg.
    plot_widget_factory: Callable[[], "QWidget"] | None = None
    # Name of the primary tab widget attribute on MainPanel (e.g. "adx134_tab").
    tab_attr: str = ""
    # {cfg_signal_name: tab_method_name} — auto-connected in the MainWindow wiring loop.
    plot_cfg_wiring: dict[str, str] = field(default_factory=dict)
    # Custom wiring callable for signals that can't be expressed as a simple dict
    # (lambdas, browse handlers, reverse wiring, etc.).
    # Signature: wire_plot_cfg(cfg_widget, main_window) -> None
    wire_plot_cfg: "Callable | None" = None


# ── Per-device factories (deferred imports break sidebar ↔ registry cycle) ────

def _rfsoc_adc_factory(state: "AppState") -> "QWidget":
    from pixels.gui.layout.sidebar import RFSoCSettingsWidget
    return RFSoCSettingsWidget()


def _adx134_plot_factory() -> "QWidget":
    from pixels.gui.layout.sidebar import ADX134PlotCfgWidget
    return ADX134PlotCfgWidget()


def _ads1219_plot_factory() -> "QWidget":
    from pixels.gui.layout.sidebar import ADS1219PlotCfgWidget
    return ADS1219PlotCfgWidget()


def _rfsoc_plot_factory() -> "QWidget":
    from pixels.gui.layout.sidebar import RFSoCPlotCfgWidget
    return RFSoCPlotCfgWidget()


# ── Per-device plot config wiring (deferred imports break circular deps) ───────

def _adx134_wire_plot_cfg(cfg: "QWidget", mw: "object") -> None:
    adx134_tab  = mw.main_panel.adx134_tab
    data_tab    = adx134_tab.data_tab
    spatial_tab = adx134_tab.spatial_tab
    # Bidirectional view switching
    cfg.view_changed.connect(adx134_tab.set_view)
    adx134_tab.view_changed.connect(cfg.set_view_silent)
    # Waveform controls
    cfg.browse_clicked.connect(mw._browse_and_load)
    cfg.channels_changed.connect(lambda: data_tab.update_channels(cfg.channels))
    cfg.port_toggled.connect(data_tab.toggle_port)
    cfg.port_show_all.connect(data_tab.show_all_ports)
    cfg.carrier_vpp_changed.connect(data_tab.set_carrier_vpp)
    # Spatial controls
    cfg.spatial_plot_mode_changed.connect(spatial_tab.set_plot_mode)
    cfg.spatial_map_path_changed.connect(spatial_tab.load_map)
    cfg.spatial_metric_changed.connect(spatial_tab.set_metric)
    cfg.spatial_window_changed.connect(spatial_tab.set_window)
    cfg.spatial_colormap_changed.connect(spatial_tab.set_colormap)
    cfg.spatial_range_changed.connect(spatial_tab.set_range)
    spatial_tab.plot_mode_changed.connect(cfg.set_spatial_plot_mode_silent)


def _ads1219_wire_plot_cfg(cfg: "QWidget", mw: "object") -> None:
    tab = mw.main_panel.ads_tab
    cfg.browse_clicked.connect(mw._browse_and_load_ads1219)
    cfg.channels_changed.connect(tab.set_visible_channels)


def _rfsoc_wire_plot_cfg(cfg: "QWidget", mw: "object") -> None:
    tab = mw.main_panel.rfsoc_tab
    cfg.browse_clicked.connect(mw._browse_and_load_rfsoc)
    cfg.plot_mode_changed.connect(
        lambda mode: tab.stop() if mode == "disabled" else None
    )


# ── Registry ──────────────────────────────────────────────────────────────────
# Insertion order determines toolbar display order.

from pixels.gui.device_schemas import ADX134_SCHEMA, ADS1219_SCHEMA  # noqa: E402

DEVICE_REGISTRY: list[DeviceDescriptor] = [
    DeviceDescriptor(
        key="adx134",
        label="ADX134",
        default_enabled=True,
        schema=ADX134_SCHEMA,
        configure_label="Configure ADX134",
        plot_widget_factory=_adx134_plot_factory,
        tab_attr="adx134_tab",
        plot_cfg_wiring={
            "plot_mode_changed":    "set_plot_mode",
            "display_mode_changed": "set_display_mode",
        },
        wire_plot_cfg=_adx134_wire_plot_cfg,
    ),
    DeviceDescriptor(
        key="ads1219",
        label="ADS1219",
        default_enabled=True,
        schema=ADS1219_SCHEMA,
        configure_label="Configure ADS1219",
        plot_widget_factory=_ads1219_plot_factory,
        tab_attr="ads_tab",
        plot_cfg_wiring={
            "plot_mode_changed":    "set_plot_mode",
            "display_mode_changed": "set_display_mode",
        },
        wire_plot_cfg=_ads1219_wire_plot_cfg,
    ),
    DeviceDescriptor(
        key="rfsoc",
        label="RFSoC",
        default_enabled=False,
        config_widget_factory=_rfsoc_adc_factory,
        plot_widget_factory=_rfsoc_plot_factory,
        tab_attr="rfsoc_tab",
        wire_plot_cfg=_rfsoc_wire_plot_cfg,
    ),
]
