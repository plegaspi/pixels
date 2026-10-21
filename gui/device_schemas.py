"""Schema definitions for per-device configurable parameters.

Each schema is a list of FieldDef instances that describe one configurable
parameter: its dotted key path into the config dict, its display label, and
what kind of widget to render.  The generic DeviceConfigWidget in
config_widget.py reads these schemas to build its form automatically.

Adding a new device: define a new *_SCHEMA list here and instantiate a
DeviceConfigWidget with it in sidebar.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ── Field descriptors ─────────────────────────────────────────────────────────

@dataclass
class ComboField:
    """Drop-down selector.  options is a list of (value, label) pairs OR a loader fn."""
    key:            str
    label:          str
    options:        list[tuple[Any, str]] | None = None
    options_loader: Callable[[], list[tuple[Any, str]]] | None = None
    advanced:       bool = False


@dataclass
class IntField:
    key:     str
    label:   str
    minimum: int = 0
    maximum: int = 2**31 - 1
    default: int = 0
    advanced: bool = False


@dataclass
class FloatField:
    key:      str
    label:    str
    minimum:  float = 0.0
    maximum:  float = 1e9
    decimals: int   = 3
    default:  float = 0.0
    suffix:   str   = ""
    advanced: bool  = False


@dataclass
class CheckField:
    key:     str
    label:   str
    default: bool = False
    advanced: bool = False


@dataclass
class ChannelMaskField:
    """Channel-selection widget: radio (differential/single-ended) + checkboxes."""
    key:              str
    label:            str
    diff_channels:    list[tuple[int, str]] = field(default_factory=list)
    se_channels:      list[tuple[int, str]] = field(default_factory=list)
    advanced:         bool = False


# Type alias used by DeviceConfigWidget
FieldDef = ComboField | IntField | FloatField | CheckField | ChannelMaskField


# ── Option loaders ────────────────────────────────────────────────────────────

def _load_odr_options() -> list[tuple[int, str]]:
    from pixels.gui.layout.sidebar import load_odr_options
    return load_odr_options()


def _load_dr_labels() -> list[tuple[int, str]]:
    from pixels.daq.ads1219.constants import DR_LABELS
    return [(k, v) for k, v in DR_LABELS.items()]


# ── ADX134 schema ─────────────────────────────────────────────────────────────

ADX134_SCHEMA: list[FieldDef] = [
    ComboField(
        key="adx134.clock_gen.odr_config",
        label="ODR Config",
        options_loader=_load_odr_options,
    ),
    ComboField(
        key="adx134.capture.mode",
        label="Capture Mode",
        options=[("raw", "Raw"), ("demod", "Demod I/Q")],
    ),
    ComboField(
        key="adx134.capture.filter_type",
        label="Filter",
        options=[
            ("sinc3",       "SINC3"),
            ("sinc6",       "SINC6"),
            ("fir",         "FIR (Wideband)"),
            ("sinc3_50_60", "SINC3 + 50/60 Hz Rej."),
        ],
    ),
    ComboField(
        key="adx134.capture.scratch_format",
        label="Scratch Format",
        options=[
            ("blosc_zstd", "blosc_zstd"),
            ("hdf5_lzf",   "hdf5_lzf"),
            ("bin",        "bin"),
        ],
        advanced=True,
    ),
    IntField(
        key="adx134.capture.write_batch",
        label="Write Batch",
        minimum=1,
        maximum=100_000,
        default=512,
        advanced=True,
    ),
    FloatField(
        key="adx134.capture.v_ref",
        label="V Ref",
        minimum=0.0,
        maximum=20.0,
        decimals=3,
        default=4.096,
        advanced=True,
    ),
    IntField(
        key="adx134.capture.resolution",
        label="Resolution (bits)",
        minimum=8,
        maximum=32,
        default=24,
        advanced=True,
    ),
    FloatField(
        key="adx134.capture.tx_enable_delay",
        label="TX Enable Delay",
        minimum=0.0,
        maximum=30.0,
        decimals=1,
        default=1.0,
        suffix=" s",
        advanced=True,
    ),
]


# ── ADS1219 schema ────────────────────────────────────────────────────────────

ADS1219_SCHEMA: list[FieldDef] = [
    ChannelMaskField(
        key="ads1219.channel_mask",
        label="Channels",
        diff_channels=[
            (0, "AIN0-AIN1"),
            (1, "AIN2-AIN3"),
            (2, "AIN1-AIN2"),
        ],
        se_channels=[
            (3, "AIN0"),
            (4, "AIN1"),
            (5, "AIN2"),
            (6, "AIN3"),
        ],
    ),
    ComboField(
        key="ads1219.gain",
        label="Gain",
        options=[(0, "1×"), (1, "4×")],
    ),
    ComboField(
        key="ads1219.data_rate",
        label="Data Rate",
        options_loader=_load_dr_labels,
    ),
]


# ── RFSoC schema ──────────────────────────────────────────────────────────────
# (connection params only; no dedicated hardware configure step)

RFSOC_SCHEMA: list[FieldDef] = []
