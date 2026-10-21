"""Shared abstract interface for post-capture data wrappers.

All device-specific data readers (ADX134Data, ADS1219Data, RFSoCData) implement
this interface so that GUI load paths and analysis scripts can operate on any
device type without branching.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class PostCaptureData(ABC):
    """Abstract base for lazy wrappers around captured HDF5 / binary data."""

    # ── Data access ───────────────────────────────────────────────────────────

    @abstractmethod
    def read_samples(self, start: int, stop: int) -> np.ndarray:
        """Return raw sample data as (stop-start, n_channels) array."""

    @abstractmethod
    def read_voltage(self, start: int, stop: int) -> np.ndarray:
        """Return calibrated voltage data as (stop-start, n_channels) float64 array."""

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def n_samples(self) -> int: ...

    @property
    @abstractmethod
    def n_channels(self) -> int: ...

    @property
    @abstractmethod
    def channel_labels(self) -> list[str]: ...

    @property
    @abstractmethod
    def sample_rate(self) -> float: ...

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "PostCaptureData":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Release any open file handles. Override in subclasses."""
