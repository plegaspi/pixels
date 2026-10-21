"""ADX134LiveReader — DataStreamBase backed by the current scratch bins."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pixels.daq._base import SampleBatch

if TYPE_CHECKING:
    from . import ADX134DAQ


class ADX134LiveReader:
    """
    DataStreamBase wrapper for a running ADX134DAQ.

    Reads the latest samples directly from the scratch HDF5/bin files
    without loading everything into memory.

    Usage:
        stream = daq.live_reader
        batch  = stream.get_latest(10_000)
    """

    def __init__(self, daq: "ADX134DAQ") -> None:
        self._daq = daq

    # ── DAQDevice protocol ────────────────────────────────────────────────────

    @property
    def n_samples(self) -> int:
        from pixels.models import ADX134Data
        try:
            d = ADX134Data(Path(self._daq.bin_dir), profile=self._daq.profile)
            return d.n_samples
        except Exception:
            return 0

    @property
    def n_channels(self) -> int:
        return self._daq.n_ports * self._daq.profile.n_chan_per_port

    @property
    def channel_labels(self) -> list[str]:
        return [f"ch{i}" for i in range(self.n_channels)]

    @property
    def sample_rate(self) -> float:
        if self._daq.odr_hz:
            return self._daq.odr_hz / max(1, self._daq.profile.demod_decimation)
        return 1_000_000.0 / max(1, self._daq.profile.demod_decimation)

    @property
    def device_metadata(self) -> dict:
        return {
            "n_ports":    self._daq.n_ports,
            "bin_dir":    self._daq.bin_dir,
            **self._daq.profile.attrs(self._daq.odr_hz),
        }

    def get_latest(self, n: int) -> SampleBatch:
        """Return the most recent n samples as a SampleBatch."""
        from pixels.models import ADX134Data
        try:
            d = ADX134Data(Path(self._daq.bin_dir), profile=self._daq.profile)
            total = d.n_samples
            start = max(0, total - n)
            data = d.read_voltage(start, total)          # (n, n_ch) float64
            time = np.arange(len(data), dtype=np.float64) / self.sample_rate
            return SampleBatch(
                data=data, time=time,
                channel_labels=self.channel_labels,
                units="V", sample_rate=self.sample_rate,
                metadata={**self.device_metadata, "demod_mode": d.demod_mode},
            )
        except Exception:
            return SampleBatch(
                data=np.empty((0, self.n_channels)),
                time=np.empty(0),
                channel_labels=self.channel_labels,
                units="V", sample_rate=self.sample_rate,
            )

    def get_range(self, start: int, stop: int) -> SampleBatch:
        """Return samples [start, stop) by absolute sample index."""
        from pixels.models import ADX134Data
        try:
            d = ADX134Data(Path(self._daq.bin_dir), profile=self._daq.profile)
            data = d.read_voltage(start, stop)
            time = np.arange(len(data), dtype=np.float64) / self.sample_rate
            return SampleBatch(
                data=data, time=time,
                channel_labels=self.channel_labels,
                units="V", sample_rate=self.sample_rate,
            )
        except Exception:
            return SampleBatch(
                data=np.empty((0, self.n_channels)),
                time=np.empty(0),
                channel_labels=self.channel_labels,
                units="V", sample_rate=self.sample_rate,
            )
