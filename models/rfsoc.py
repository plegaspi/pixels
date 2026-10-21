"""RFSoC acquisition data wrapper — lazy access to saved HDF5 captures."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pixels.daq.rfsoc.constants import CHANNEL_LABELS
from pixels.models._base import PostCaptureData


class RFSoCData(PostCaptureData):
    """
    Lazy wrapper for an rfsoc capture HDF5 file.

    Follows the same interface as ADS1219Data and ADX134Data so the GUI
    load path can use a consistent API.

    The underlying dataset stores signed 16-bit ADC codes.  ``read_voltage``
    normalises to the full-scale range (±1.0) as a convenience; use
    ``read_samples`` for the raw counts.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._f    = None

    def _open(self):
        import h5py
        if self._f is None or not self._f.id.valid:
            self._f = h5py.File(self._path, "r")
        return self._f

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    def n_channels(self) -> int:
        return int(self._open()["samples"].attrs.get("n_channels", 1))

    @property
    def n_samples(self) -> int:
        return self._open()["samples"].shape[0]

    @property
    def sample_rate(self) -> float:
        return float(self._open()["samples"].attrs.get("sample_rate", 0.0))

    @property
    def channel_labels(self) -> list[str]:
        raw = self._open()["samples"].attrs.get("channel_labels", None)
        if raw is not None:
            return list(raw)
        return list(CHANNEL_LABELS[:self.n_channels])

    # ── Data access ───────────────────────────────────────────────────────────

    def read_samples(self, start: int, stop: int) -> np.ndarray:
        """Return (stop-start, n_channels) int16 raw ADC codes."""
        return np.array(self._open()["samples"][start:stop], dtype=np.int16)

    def read_voltage(self, start: int, stop: int) -> np.ndarray:
        """
        Return (stop-start, n_channels) float64 normalised to ±1.0.

        Divides raw codes by 32768 (full-scale for a signed 16-bit ADC).
        Multiply by the actual full-scale voltage if needed.
        """
        raw = self.read_samples(start, stop).astype(np.float64)
        return raw / 32768.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def __repr__(self) -> str:
        try:
            return (
                f"RFSoCData({self.n_samples:,} samples × {self.n_channels} ch "
                f"@ {self.sample_rate:,.0f} Hz, path={self._path.name!r})"
            )
        except Exception:
            return f"RFSoCData(path={self._path.name!r})"
