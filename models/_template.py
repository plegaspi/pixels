"""
Template: data model for a new DAQ device.
Copy to src/pixels/models/<device>.py and replace every TODO.
See docs/gui/updates.md for the full integration checklist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


class TemplateData:
    """
    Lazy wrapper for a saved <device> HDF5 file.

    Mirrors the interface of ADS1219Data / ADX134Data so the GUI load path
    (DataTab / custom plot tab) can use a consistent API.

    Replace `TemplateData` with your device name.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._f    = None   # h5py.File opened lazily

    # ── Lazy file access ──────────────────────────────────────────────────────

    def _open(self):
        import h5py
        if self._f is None or not self._f.id.valid:
            self._f = h5py.File(self._path, "r")
        return self._f

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    def n_channels(self) -> int:
        # TODO: read from file attrs or derive from data shape
        raise NotImplementedError

    @property
    def n_samples(self) -> int:
        # TODO: read from file attrs or dataset shape
        raise NotImplementedError

    # ── Data access ───────────────────────────────────────────────────────────

    def read_voltage(self, start: int, stop: int) -> np.ndarray:
        """
        Return (stop-start, n_channels) float64 in volts.

        This is the primary method called by the GUI load path and by
        get_latest() in the DAQ live reader.

        TODO: open self._path, read the relevant dataset slice, convert to volts.
        """
        # Example pattern:
        #
        # f = self._open()
        # raw = np.array(f["raw"][start:stop, :], dtype=np.int32)
        # lsb = float(f.attrs.get("lsb", LSB))
        # return raw.astype(np.float64) * lsb
        raise NotImplementedError

    def read_samples(self, start: int, stop: int) -> np.ndarray:
        """
        Return (stop-start, n_channels) int32 raw ADC codes.

        TODO: implement if raw-count display mode is needed in the GUI.
        """
        raise NotImplementedError

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def __enter__(self) -> "TemplateData":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __repr__(self) -> str:
        try:
            return (
                f"TemplateData(path={self._path.name!r}, "
                f"{self.n_samples:,} samples × {self.n_channels} channels)"
            )
        except Exception:
            return f"TemplateData(path={self._path.name!r})"
