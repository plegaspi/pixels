"""
Common DAQ abstract base class and data types.

Usage:
    from pixels.daq import DAQDevice, SampleBatch

    def record(device: DAQDevice, secs: float) -> SampleBatch:
        return device.collect(secs)

    # Works with any conforming device:
    record(adc)  # ADS1219
    record(daq)  # ADX134DAQ
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class SampleBatch:
    """
    Device-agnostic container for a block of acquired samples.

    data            (n_samples, n_channels)  float64
    time            (n_samples,)             seconds from acquisition start,
                                             or sample index if sample_rate == 0
    channel_labels  one label per channel column in data
    units           physical unit string for data values
    sample_rate     Hz; 0 if time is index-based
    metadata        device-specific extras (gain, data_rate, n_ports, …)
    """
    data:           np.ndarray
    time:           np.ndarray
    channel_labels: list[str]
    units:          str   = "V"
    sample_rate:    float = 0.0
    metadata:       dict  = field(default_factory=dict)

    @property
    def n_samples(self) -> int:
        return len(self.data)

    @property
    def n_channels(self) -> int:
        return self.data.shape[1] if self.data.ndim == 2 else 1

    def channel(self, label: str) -> np.ndarray:
        """Return the 1-D array for a named channel."""
        idx = self.channel_labels.index(label)
        return self.data[:, idx]


class DAQDevice(ABC):
    """Abstract base for all DAQ devices."""

    # ── Abstract: control ────────────────────────────────────────────────────

    @abstractmethod
    def start(self, save_path: str | None = None) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...

    # ── Abstract: stream ─────────────────────────────────────────────────────

    @property
    @abstractmethod
    def n_samples(self) -> int: ...

    @property
    @abstractmethod
    def sample_rate(self) -> float: ...

    @property
    @abstractmethod
    def n_channels(self) -> int: ...

    @property
    @abstractmethod
    def channel_labels(self) -> list[str]: ...

    @property
    @abstractmethod
    def device_metadata(self) -> dict: ...

    @abstractmethod
    def get_latest(self, n: int) -> SampleBatch: ...

    @abstractmethod
    def get_range(self, start: int, stop: int) -> SampleBatch: ...

    @abstractmethod
    def configure(self, config: dict) -> None:
        """Apply a (partial) config dict to this device's runtime parameters.

        Each device implements this to pick the keys it cares about.  For
        devices that own a hardware connection (ADS1219 serial), this also
        pushes settings to the hardware.  For devices where the hardware side
        is managed externally (ADX134 UDP control layer), this only updates
        in-memory parameters.
        """

    # ── Concrete shared methods ───────────────────────────────────────────────

    def collect(self, n_seconds: float, save_path: str | None = None, **kwargs) -> SampleBatch:
        """Start acquisition, wait n_seconds, stop, return collected samples.

        Extra kwargs (e.g. use_processes=True) are forwarded to start().
        """
        self.start(save_path=save_path, **kwargs)
        time.sleep(n_seconds)
        self.stop()
        n = int(n_seconds * self.sample_rate) if self.sample_rate else self.n_samples
        return self.get_latest(n)

    def __enter__(self) -> DAQDevice:
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
