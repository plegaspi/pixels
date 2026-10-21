"""ADX134 acquisition data wrapper — lazy packet unpacking without upfront conversion."""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Optional

import numpy as np

from pixels.daq.adx134.data.conversion import PACKED_HEADER_WORDS
from pixels.daq.adx134.profiles import (
    ADX134Profile,
    get_adx134_profile,
    profile_from_config,
)
from pixels.models._base import PostCaptureData


class ADX134Data(PostCaptureData):
    """
    Wraps a bins/ directory and unpacks ADC codes on demand.

    Replicates the sync + unpack logic of convert_bin_to_hdf5 but lazily —
    only reads the requested packet range from disk on each call.

    Parameters
    ----------
    bins_dir : Path
        Directory containing capture_port*.h5 / capture_port*.bin files.
    demod_mode : bool | None
        If None, reads from data.yaml. Set explicitly to override.
    """

    def __init__(
        self,
        bins_dir: Path,
        demod_mode: Optional[bool] = None,
        profile: ADX134Profile | str | None = None,
    ) -> None:
        from pixels.daq.adx134.packet_store import PacketReader

        self._bins_dir = bins_dir
        self._readers: list[PacketReader] = []
        self._start_idxs: Optional[list[int]] = None
        self._n_pkts_common: Optional[int] = None
        if isinstance(profile, str):
            self._profile = get_adx134_profile(profile)
        elif profile is not None:
            self._profile = profile
        else:
            try:
                from pixels.configs import load_data_yaml

                self._profile = profile_from_config(load_data_yaml())
            except Exception:
                self._profile = get_adx134_profile(demod_mode=demod_mode)

        paths = sorted(
            glob.glob(str(bins_dir / "capture_port*.h5"))
            + glob.glob(str(bins_dir / "capture_port*.bin"))
        )
        for p in paths:
            reader = PacketReader(
                p, swmr=p.endswith((".h5", ".hdf5")), profile=self._profile
            )
            self._profile = reader.profile
            self._readers.append(reader)

    # ── Sync ──────────────────────────────────────────────────────────────────

    def _ensure_synced(self) -> None:
        """Compute per-port start-packet offsets once (sync by frame_seq + trim warm-up)."""
        if self._start_idxs is not None:
            return

        if any(r.n_packets == 0 for r in self._readers):
            self._start_idxs = [0] * len(self._readers)
            self._n_pkts_common = 0
            return

        first_seqs = [r.frame_seq(0) for r in self._readers]
        target_seq = max(first_seqs)

        start_idxs = []
        for i, (reader, fs) in enumerate(zip(self._readers, first_seqs)):
            if fs == target_seq:
                si = 0
            else:
                matches = reader.find_frame_seq(target_seq)
                if len(matches) == 0:
                    raise ValueError(f"Port {i}: frame_seq={target_seq} not found")
                si = int(matches[0])

            # Trim initial discontinuities (FPGA warm-up packets)
            probe = min(256, reader.n_packets - si)
            seqs = reader.frame_seqs(si, si + probe)
            diffs = np.diff(seqs)
            bad = np.where((diffs % 256) != 1)[0]
            if len(bad) > 0 and bad[0] < 16:
                si += int(bad[0]) + 1

            start_idxs.append(si)

        self._start_idxs = start_idxs
        self._n_pkts_common = min(
            r.n_packets - si for r, si in zip(self._readers, start_idxs)
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def n_ports(self) -> int:
        return len(self._readers)

    @property
    def n_channels(self) -> int:
        return self.n_ports * self.n_chan_per_port

    @property
    def n_samples(self) -> int:
        self._ensure_synced()
        return self._n_pkts_common * self.samples_per_frame

    @property
    def profile(self) -> ADX134Profile:
        return self._profile

    @property
    def n_chan_per_port(self) -> int:
        return self._profile.n_chan_per_port

    @property
    def samples_per_frame(self) -> int:
        return self._profile.samples_per_frame

    @property
    def payload_bytes(self) -> int:
        return self._profile.payload_bytes

    @property
    def demod_mode(self) -> bool:
        return self._profile.demod_mode

    @property
    def readers(self):
        return self._readers

    @property
    def channel_labels(self) -> list[str]:
        cpp = self.n_chan_per_port
        return [f"p{p}ch{c}" for p in range(self.n_ports) for c in range(cpp)]

    @property
    def sample_rate(self) -> float:
        """Sample rate in Hz, read from profile odr_hz attr if available."""
        odr = getattr(self._profile, "odr_hz", None)
        return float(odr) if odr else 0.0

    def n_packets(self, port: int) -> int:
        return self._readers[port].n_packets

    def read_words(self, port: int, start: int, stop: int) -> np.ndarray:
        """Raw 32-bit words for packets [start, stop) on a single port."""
        return self._readers[port].read_words(start, stop)

    def frame_seqs(
        self, port: int, start: int = 0, stop: Optional[int] = None
    ) -> np.ndarray:
        return self._readers[port].frame_seqs(start, stop)

    # ── Unpack ────────────────────────────────────────────────────────────────

    def _unpack_packets(self, pkt_start: int, pkt_stop: int) -> np.ndarray:
        """
        Unpack packets [pkt_start, pkt_stop) across all ports.
        Returns (n_pkts * samples_per_frame, n_ports * n_chan_per_port) int32.
        """
        self._ensure_synced()
        chunk_pkts = pkt_stop - pkt_start
        spf = self.samples_per_frame
        cpp = self.n_chan_per_port
        packed_data_bytes = spf * cpp * 3
        row = np.empty((chunk_pkts * spf, self.n_channels), dtype=np.int32)

        for port_idx, (reader, si) in enumerate(zip(self._readers, self._start_idxs)):
            words = reader.read_words(si + pkt_start, si + pkt_stop)
            payload_words = words[:, PACKED_HEADER_WORDS:].astype(">u4", copy=False)
            payload_bytes = payload_words.view(np.uint8).reshape(
                chunk_pkts, packed_data_bytes
            )
            byte_codes = payload_bytes.reshape(chunk_pkts, spf, cpp, 3).astype(np.int32)
            codes = (
                (byte_codes[:, :, :, 0] << 16)
                | (byte_codes[:, :, :, 1] << 8)
                | byte_codes[:, :, :, 2]
            )
            codes[codes >= 0x800000] -= 0x1000000
            offset = port_idx * cpp
            row[:, offset : offset + cpp] = codes.reshape(chunk_pkts * spf, cpp)
        return row

    def _sample_range_to_raw(self, start: int, stop: int) -> np.ndarray:
        """Convert sample indices to packet range and unpack, trimming to exact samples."""
        spf = self.samples_per_frame
        p_start = start // spf
        p_stop = (stop + spf - 1) // spf
        raw = self._unpack_packets(p_start, p_stop)
        off = start % spf
        return raw[off : off + (stop - start)]

    # ── Public accessors ──────────────────────────────────────────────────────

    def read_samples(self, start: int, stop: int) -> np.ndarray:
        """(stop-start, n_channels) int32 ADC codes."""
        return self._sample_range_to_raw(start, stop)

    def read_voltage(self, start: int, stop: int) -> np.ndarray:
        """(stop-start, n_channels) float64 volts."""
        from pixels.daq.adx134.data.constants import LSB

        return self._sample_range_to_raw(start, stop).astype(np.float64) * LSB

    def read_amplitude_decimated(
        self, start: int, stop: int, decimation: int = 25
    ) -> np.ndarray:
        """
        Envelope magnitude averaged over `decimation` samples.
        Default 25× gives 125 kHz -> 5 kHz output.
        Returns (n_out, n_real_channels) float64.
        f_s = 125_000 / decimation
        """
        amp = self.read_amplitude(start, stop)
        n = (len(amp) // decimation) * decimation
        return amp[:n].reshape(-1, decimation, amp.shape[1]).mean(axis=1)

    def read_amplitude(self, start: int, stop: int) -> np.ndarray:
        """
        Demod mode: sqrt(I^2 + Q^2) * LSB.
        Returns (stop-start, n_ports * n_chan_per_port // 2) float64.
        """
        if not self.demod_mode:
            raise ValueError("read_amplitude requires demod_mode=True")
        from pixels.daq.adx134.data.constants import LSB

        raw = self._sample_range_to_raw(start, stop).astype(np.float64)
        I, Q = self._split_iq(raw)
        return np.sqrt(I**2 + Q**2) * LSB

    def read_phase(self, start: int, stop: int) -> np.ndarray:
        """
        Demod mode: arctan2(Q, I) in radians.
        Returns (stop-start, n_ports * n_chan_per_port // 2) float64.
        """
        if not self.demod_mode:
            raise ValueError("read_phase requires demod_mode=True")
        raw = self._sample_range_to_raw(start, stop).astype(np.float64)
        I, Q = self._split_iq(raw)
        return np.arctan2(Q, I)

    def _split_iq(self, raw: np.ndarray):
        """
        Split demod data into I and Q arrays.
        Layout per port: slots 0..N/2-1 = I,  N/2..N-1 = Q.
        """
        cpp = self.n_chan_per_port
        n_half = cpp // 2
        n_p = self.n_ports
        i_cols = [p * cpp + c for p in range(n_p) for c in range(n_half)]
        q_cols = [p * cpp + n_half + c for p in range(n_p) for c in range(n_half)]
        return raw[:, i_cols], raw[:, q_cols]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        for r in self._readers:
            r.close()
        self._readers.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __repr__(self) -> str:
        try:
            self._ensure_synced()
            return (
                f"ADX134Data({self.n_ports} port(s), "
                f"{self.n_samples:,} samples × {self.n_channels} channels, "
                f"mode={'demod' if self.demod_mode else 'raw'})"
            )
        except Exception:
            return f"ADX134Data({self.n_ports} port(s), not yet synced)"
