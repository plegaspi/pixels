"""Schema descriptors for plot-config sidebar widgets.

Each PlotFieldDef variant drives rendering in PlotConfigWidget.  Device-specific
schemas are defined at the bottom and imported by sidebar.py and device_registry.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlotModeField:
    """Row of radio buttons: live / load / disabled (or a subset)."""
    modes: list[str] = field(default_factory=lambda: ["live", "load", "disabled"])


@dataclass
class FileBrowseField:
    """Read-only path display + Browse button.
    Automatically hidden unless the active mode is 'load'."""
    placeholder: str = "No file loaded"
    label: str = ""  # optional heading shown above the row


@dataclass
class DisplayModeField:
    """Combo box for choosing what to display.
    options is a list of (signal_value, visible_label) pairs."""
    options: list[tuple[str, str]] = field(default_factory=list)


PlotFieldDef = PlotModeField | FileBrowseField | DisplayModeField


# ── ADX134 ────────────────────────────────────────────────────────────────────

ADX134_RAW_DISPLAY_OPTIONS: list[tuple[str, str]] = [
    ("voltage",  "Voltage"),
    ("codes",    "ADC Codes"),
    ("both",     "Voltage + ADC Codes"),
]

ADX134_DEMOD_DISPLAY_OPTIONS: list[tuple[str, str]] = [
    ("iq",               "I/Q Signal"),
    ("amplitude",        "Amplitude"),
    ("phase",            "Phase"),
    ("amplitude_phase",  "Amplitude + Phase"),
    ("iq_amplitude",     "I/Q + Amplitude"),
    ("iq_phase",         "I/Q + Phase"),
]

ADX134_WAVEFORM_SCHEMA: list[PlotFieldDef] = [
    PlotModeField(modes=["live", "load", "disabled"]),
    FileBrowseField(),
    DisplayModeField(options=ADX134_RAW_DISPLAY_OPTIONS),
]

ADX134_SPATIAL_SCHEMA: list[PlotFieldDef] = [
    PlotModeField(modes=["live", "load"]),
    FileBrowseField(placeholder="No map loaded", label="Channel Map:"),
]

# ── ADS1219 ───────────────────────────────────────────────────────────────────

ADS1219_PLOT_SCHEMA: list[PlotFieldDef] = [
    PlotModeField(modes=["live", "load", "disabled"]),
    FileBrowseField(),
    DisplayModeField(options=[("voltage", "Voltage"), ("raw", "Raw counts")]),
]

# ── RFSoC ─────────────────────────────────────────────────────────────────────

RFSOC_PLOT_SCHEMA: list[PlotFieldDef] = [
    PlotModeField(modes=["live", "disabled"]),
    FileBrowseField(),
]
