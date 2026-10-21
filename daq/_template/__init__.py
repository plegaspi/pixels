"""
Template: main DAQ device class.
Copy this entire _template/ directory to src/pixels/daq/<device>/ and replace every TODO.
See docs/gui/updates.md for the full integration checklist.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from pixels.daq._base import DAQDevice, SampleBatch

# TODO: uncomment once constants.py is filled in
# from .constants import CHANNEL_LABELS, N_CHANNELS, SAMPLE_RATE, LSB
# from .receiver import Sample
# from .packet_store import save_hdf5


class TemplateDAQ(DAQDevice):
    """
    Stub for a new DAQ device.

    Replace `TemplateDAQ` with your device name and implement every method
    that raises NotImplementedError.  Methods marked with a skeleton comment
    have the right shape — only the TODO values need replacing.
    """

    def __init__(
        self,
        # TODO: add connection parameters (port, baud, host_ip, etc.)
        max_samples: int = 100_000,
    ):
        self._running = False
        self._save_path: Optional[str] = None
        self._max_samples = max_samples
        # TODO: initialise connection object and ring buffer

    # ── Connection (serial / UDP / etc.) ─────────────────────────────────────
    # Remove this section if the device does not have a separate connect step.
    # ADS1219 uses connect()/disconnect(); ADX134 binds sockets inside start().

    def connect(self) -> None:
        """Open the connection and start the background parse / receive thread."""
        raise NotImplementedError

    def disconnect(self) -> None:
        """Stop streaming, then close the connection."""
        self.stop()
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    # ── DAQDevice: control ────────────────────────────────────────────────────

    def start(self, save_path: Optional[str] = None) -> None:
        """Begin acquisition. If save_path given, auto-save to HDF5 on stop()."""
        self._save_path = save_path
        self._running = True
        # TODO: send start command / enable data flow
        raise NotImplementedError

    def stop(self) -> None:
        """Stop acquisition and auto-save if save_path was set."""
        self._running = False
        # TODO: send stop command
        if self._save_path and self.n_samples > 0:
            self.save(self._save_path)
            self._save_path = None

    @property
    def is_running(self) -> bool:
        return self._running

    # ── DAQDevice: stream metadata ────────────────────────────────────────────

    @property
    def n_samples(self) -> int:
        # TODO: return total accumulated sample count from ring buffer
        raise NotImplementedError

    @property
    def sample_rate(self) -> float:
        # TODO: return SAMPLE_RATE
        raise NotImplementedError

    @property
    def n_channels(self) -> int:
        # TODO: return N_CHANNELS
        raise NotImplementedError

    @property
    def channel_labels(self) -> list[str]:
        # TODO: return CHANNEL_LABELS  (or derive from active channels)
        raise NotImplementedError

    @property
    def device_metadata(self) -> dict:
        # TODO: return device-specific extras (gain, data_rate, …)
        return {}

    # ── DAQDevice: data access ────────────────────────────────────────────────

    def get_latest(self, n: int) -> SampleBatch:
        """Return the most recent n samples as a SampleBatch."""
        # TODO: read from ring buffer, convert to (n_samples, n_channels) float64
        #
        # Minimal pattern:
        #   data = <(n, n_channels) float64 in volts>
        #   time = <(n,) float64 seconds from start, or np.arange(n) / sample_rate>
        #   return SampleBatch(
        #       data=data, time=time,
        #       channel_labels=self.channel_labels,
        #       units="V", sample_rate=self.sample_rate,
        #       metadata=self.device_metadata,
        #   )
        raise NotImplementedError

    def get_range(self, start: int, stop: int) -> SampleBatch:
        """Return samples [start, stop) by absolute sample index."""
        # Simple default: delegate to get_latest for a fixed window size.
        # Override if the ring buffer supports random access.
        return self.get_latest(stop - start)

    # ── Additional API ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Discard all buffered samples."""
        # TODO: clear ring buffer
        raise NotImplementedError

    def save(self, path: str) -> None:
        """Write buffered samples to HDF5 via packet_store.save_hdf5."""
        # TODO: uncomment once packet_store.py is implemented
        # save_hdf5(self._buf, path)
        raise NotImplementedError

    # ── Context manager ───────────────────────────────────────────────────────
    # Override __enter__ / __exit__ only if the device needs steps beyond
    # start() / stop() (e.g. connect + init before start).
    # If start() is self-contained, remove these and let DAQDevice handle it.

    def __enter__(self) -> "TemplateDAQ":
        self.connect()   # TODO: add init() call if needed
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
        self.disconnect()
