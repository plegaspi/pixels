"""ADS1219 acquisition data wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from pixels.models._base import PostCaptureData

_DIFFERENTIAL_MUXES = frozenset({0, 1, 2})  # AIN0-AIN1, AIN2-AIN3, AIN1-AIN2


class ADS1219Channel:
    """One MUX channel from an ads1219.h5 group."""

    def __init__(self, group) -> None:
        self._grp      = group
        self.mux       = int(group.attrs.get("mux", 0))
        self.n_samples = int(group.attrs.get("n_samples", len(group["raw"])))

    @property
    def time(self) -> np.ndarray:
        return np.asarray(self._grp["time"], dtype=np.float64)

    @property
    def raw(self) -> np.ndarray:
        return np.asarray(self._grp["raw"], dtype=np.int32)

    @property
    def voltage(self) -> np.ndarray:
        return np.asarray(self._grp["voltage"], dtype=np.float64)

    @property
    def label(self) -> str:
        from pixels.daq.ads1219.constants import MUX_LABELS
        return MUX_LABELS.get(self.mux, f"MUX{self.mux}")

    @property
    def is_differential(self) -> bool:
        return self.mux in _DIFFERENTIAL_MUXES

    @property
    def input_type(self) -> str:
        return "differential" if self.is_differential else "single-ended"

    def __len__(self) -> int:
        return self.n_samples

    def __repr__(self) -> str:
        return (
            f"ADS1219Channel(mux={self.mux}, {self.input_type}, "
            f"label='{self.label}', n_samples={self.n_samples})"
        )


class ADS1219Data(PostCaptureData):
    """Lazy wrapper for ads1219.h5.

    Implements PostCaptureData by merging per-channel samples into a common
    time-aligned array (shortest channel defines n_samples).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._f    = None

    def _open(self):
        import h5py
        if self._f is None or not self._f.id.valid:
            self._f = h5py.File(self._path, "r")
        return self._f

    @property
    def channels(self) -> dict[int, ADS1219Channel]:
        f = self._open()
        return {
            int(k[2:]): ADS1219Channel(f[k])
            for k in sorted(f.keys()) if k.startswith("ch")
        }

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def n_samples(self) -> int:
        chs = self.channels
        if not chs:
            return 0
        return min(ch.n_samples for ch in chs.values())

    @property
    def total_samples(self) -> int:
        return sum(ch.n_samples for ch in self.channels.values())

    @property
    def sample_rate(self) -> float:
        """Approximate sample rate (all channels share the same DR setting)."""
        f = self._open()
        return float(f.attrs.get("sample_rate_hz", 0.0))

    @property
    def channel_labels(self) -> list[str]:
        return [ch.label for ch in self.channels.values()]

    def read_samples(self, start: int, stop: int) -> np.ndarray:
        """Return raw int32 codes for all channels, (stop-start, n_channels)."""
        chs = self.channels
        if not chs:
            return np.empty((0, 0), dtype=np.int32)
        return np.column_stack([
            ch.raw[start:stop] for ch in chs.values()
        ])

    def read_voltage(self, start: int, stop: int) -> np.ndarray:
        """Return voltage data for all channels, (stop-start, n_channels) float64."""
        chs = self.channels
        if not chs:
            return np.empty((0, 0), dtype=np.float64)
        return np.column_stack([
            ch.voltage[start:stop] for ch in chs.values()
        ])

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def __repr__(self) -> str:
        chs = self.channels
        return (
            f"ADS1219Data({len(chs)} channel(s), "
            f"{sum(c.n_samples for c in chs.values()):,} total samples)"
        )
