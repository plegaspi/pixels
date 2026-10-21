"""RFSoC ADC stream DAQ — listens for TCP or UDP frames from the RFSoC Vitis example."""

from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from pixels._log import get_logger
from pixels.daq._base import DAQDevice, SampleBatch
from .constants import (
    CHANNEL_LABELS,
    TCP_HEADER,
    UDP_HEADER,
    UDP_STALE_TIMEOUT_S,
)
from .receiver import (
    apply_lane_order,
    channel_count_from_header,
    payload_to_samples,
    recv_exact,
    select_word_lane,
)
from .packet_store import save_hdf5

_log = get_logger(__name__)


class RFSoCDAQ(DAQDevice):
    """
    Listens for ADC frames streamed from an RFSoC device over TCP or UDP.

    The device sends frames when triggered externally; this class only
    listens and accumulates data. Call start() to arm the receiver, then
    wait for data to arrive.

    Parameters
    ----------
    bind : str
        Local interface to bind (default "0.0.0.0").
    port : int
        Local port to listen on (default 5000).
    mode : str
        "tcp" or "udp".
    max_samples : int
        Ring buffer capacity in samples; oldest frames evicted when full.
    lanes_per_word : int
        Number of 16-bit samples in each RFDC AXIS word (default 8).
    lane_order : str
        "lsb-first" or "msb-first" — order of lanes within an AXIS word.
    word_lane : int or None
        If set, keep only this lane index, dividing effective sample rate
        by lanes_per_word.
    """

    def __init__(
        self,
        bind: str = "0.0.0.0",
        port: int = 5000,
        mode: str = "tcp",
        max_samples: int = 1_000_000,
        lanes_per_word: int = 8,
        lane_order: str = "lsb-first",
        word_lane: Optional[int] = None,
    ):
        if mode not in ("tcp", "udp"):
            raise ValueError(f"mode must be 'tcp' or 'udp', got {mode!r}")
        if word_lane is not None and not 0 <= word_lane < lanes_per_word:
            raise ValueError(f"word_lane must be in [0, {lanes_per_word - 1}]")

        self._bind = bind
        self._port = port
        self._mode = mode
        self._max_samples = max_samples
        self._lanes_per_word = lanes_per_word
        self._lane_order = lane_order
        self._word_lane = word_lane

        self._running = False
        self._stop_event = threading.Event()
        self._recv_thread: Optional[threading.Thread] = None
        self._save_path: Optional[str] = None

        # Ring buffer: deque of (n_samples, n_channels) int16 arrays
        self._frames: deque[np.ndarray] = deque()
        self._total_samples: int = 0
        self._sample_rate: float = 0.0
        self._n_channels: int = 0
        self._lock = threading.Lock()

    # ── DAQDevice protocol ────────────────────────────────────────────────────

    def start(self, save_path: Optional[str] = None) -> None:
        """Bind the socket and begin listening for incoming frames."""
        if self._running:
            return
        self._save_path = save_path
        self._stop_event.clear()
        self._running = True
        target = self._recv_tcp if self._mode == "tcp" else self._recv_udp
        self._recv_thread = threading.Thread(
            target=target, daemon=True, name="rfsoc-recv"
        )
        self._recv_thread.start()
        _log.info(
            "RFSoC: listening for %s frames on %s:%d",
            self._mode.upper(), self._bind, self._port,
        )

    def stop(self) -> None:
        """Stop listening and auto-save if a save_path was set."""
        self._running = False
        self._stop_event.set()
        if self._recv_thread is not None:
            self._recv_thread.join(timeout=5.0)
            self._recv_thread = None
        if self._save_path and self._total_samples > 0:
            self.save(self._save_path)
            self._save_path = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def n_samples(self) -> int:
        with self._lock:
            return self._total_samples

    @property
    def sample_rate(self) -> float:
        with self._lock:
            sr = self._sample_rate
        if self._word_lane is not None and sr > 0:
            return sr / self._lanes_per_word
        return sr

    @property
    def n_channels(self) -> int:
        with self._lock:
            return self._n_channels if self._n_channels > 0 else len(CHANNEL_LABELS)

    @property
    def channel_labels(self) -> list[str]:
        with self._lock:
            n = self._n_channels
        return list(CHANNEL_LABELS[:n]) if n > 0 else list(CHANNEL_LABELS)

    @property
    def device_metadata(self) -> dict:
        return {
            "bind":          self._bind,
            "port":          self._port,
            "mode":          self._mode,
            "lane_order":    self._lane_order,
            "lanes_per_word": self._lanes_per_word,
            "word_lane":     self._word_lane,
        }

    def get_latest(self, n: int) -> SampleBatch:
        """Return the most recent n samples as a SampleBatch."""
        with self._lock:
            if not self._frames:
                n_ch = self._n_channels or len(CHANNEL_LABELS)
                return SampleBatch(
                    data=np.empty((0, n_ch), dtype=np.float64),
                    time=np.empty(0),
                    channel_labels=list(CHANNEL_LABELS[:n_ch]),
                    units="counts",
                    sample_rate=self.sample_rate,
                )

            # Walk from newest to oldest, collecting until we have n samples.
            collected: list[np.ndarray] = []
            total = 0
            for frame in reversed(self._frames):
                if total >= n:
                    break
                collected.append(frame)
                total += len(frame)

            collected.reverse()
            data = np.concatenate(collected, axis=0)
            if len(data) > n:
                data = data[-n:]

            sr = self.sample_rate
            labels = list(CHANNEL_LABELS[:data.shape[1]])

        t = np.arange(len(data), dtype=np.float64) / sr if sr > 0 else np.arange(len(data), dtype=np.float64)
        return SampleBatch(
            data=data.astype(np.float64),
            time=t,
            channel_labels=labels,
            units="counts",
            sample_rate=sr,
            metadata=self.device_metadata,
        )

    def get_range(self, start: int, stop: int) -> SampleBatch:
        """Return samples [start, stop) by absolute sample index."""
        return self.get_latest(stop - start)

    def configure(self, config: dict) -> None:
        """Update connection parameters from config dict.

        Reads connections.rfsoc.bind, .port, and .mode keys.
        Only takes effect on the next start() call.
        """
        rfsoc = config.get("connections", {}).get("rfsoc", {})
        if rfsoc.get("bind"):
            self._bind = rfsoc["bind"]
        if rfsoc.get("port"):
            self._port = int(rfsoc["port"])
        if rfsoc.get("mode") in ("tcp", "udp"):
            self._mode = rfsoc["mode"]

    # ── Additional API ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Discard all buffered samples."""
        with self._lock:
            self._frames.clear()
            self._total_samples = 0

    def save(self, path: str) -> None:
        """Write all buffered samples to an HDF5 file."""
        with self._lock:
            if not self._frames:
                return
            data = np.concatenate(list(self._frames), axis=0)
            sr = self._sample_rate
            n_ch = self._n_channels

        save_hdf5(
            data,
            path,
            sample_rate=sr,
            channel_labels=list(CHANNEL_LABELS[:n_ch]),
            metadata=self.device_metadata,
        )
        _log.info("RFSoC: saved %d samples to %s", len(data), path)

    # ── Internal: frame ingestion ─────────────────────────────────────────────

    def _process_payload(
        self, payload: bytes, channel_count: int, sample_rate_hz: float
    ) -> None:
        samples = payload_to_samples(payload, channel_count)
        samples = apply_lane_order(samples, self._lane_order, self._lanes_per_word)
        samples = select_word_lane(samples, self._word_lane, self._lanes_per_word)
        if samples.ndim == 1:
            samples = samples[:, np.newaxis]
        if len(samples) == 0:
            return
        self._add_frame(samples, sample_rate_hz)

    def _add_frame(self, samples: np.ndarray, sample_rate: float) -> None:
        with self._lock:
            self._sample_rate = sample_rate
            self._n_channels = samples.shape[1]
            self._frames.append(samples)
            self._total_samples += len(samples)
            # Evict oldest frames to stay within max_samples.
            while self._total_samples > self._max_samples and len(self._frames) > 1:
                evicted = self._frames.popleft()
                self._total_samples -= len(evicted)

    # ── Internal: TCP receiver ────────────────────────────────────────────────

    def _recv_tcp(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.settimeout(1.0)
                srv.bind((self._bind, self._port))
                srv.listen(1)
                while not self._stop_event.is_set():
                    try:
                        conn, addr = srv.accept()
                    except socket.timeout:
                        continue
                    _log.info("RFSoC: TCP connection from %s:%d", *addr)
                    with conn:
                        self._handle_tcp_connection(conn)
        except Exception as exc:
            _log.error("RFSoC TCP receiver error: %s", exc)
        finally:
            self._running = False

    def _handle_tcp_connection(self, conn: socket.socket) -> None:
        while not self._stop_event.is_set():
            try:
                header_bytes = recv_exact(conn, TCP_HEADER.size)
            except ConnectionError:
                break

            magic, version, header_size, frame_id, sample_rate_hz, n_samples, payload_bytes = (
                TCP_HEADER.unpack(header_bytes)
            )
            channel_count = channel_count_from_header(magic, version, "tcp")
            if channel_count == 0 or header_size != TCP_HEADER.size:
                _log.warning("RFSoC: invalid TCP frame header (magic=%r)", magic)
                break

            expected = n_samples * channel_count * 2
            if payload_bytes != expected:
                _log.warning(
                    "RFSoC: TCP payload size %d does not match expected %d",
                    payload_bytes, expected,
                )
                break

            try:
                payload = recv_exact(conn, payload_bytes)
            except ConnectionError:
                break

            self._process_payload(payload, channel_count, float(sample_rate_hz))

    # ── Internal: UDP receiver ────────────────────────────────────────────────

    def _recv_udp(self) -> None:
        pending: dict[int, dict] = {}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(1.0)
                sock.bind((self._bind, self._port))
                while not self._stop_event.is_set():
                    try:
                        packet, _ = sock.recvfrom(65535)
                    except socket.timeout:
                        self._expire_stale_udp(pending)
                        continue

                    if len(packet) < UDP_HEADER.size:
                        continue

                    (magic, version, header_size, frame_id, sample_rate_hz,
                     n_samples, chunk_index, chunk_count, payload_offset,
                     chunk_bytes) = UDP_HEADER.unpack(packet[:UDP_HEADER.size])

                    channel_count = channel_count_from_header(magic, version, "udp")
                    if channel_count == 0 or header_size != UDP_HEADER.size:
                        continue

                    payload = packet[UDP_HEADER.size:]
                    if len(payload) != chunk_bytes:
                        continue

                    frame = pending.get(frame_id)
                    if frame is None:
                        frame = {
                            "sample_rate_hz": float(sample_rate_hz),
                            "n_samples":      n_samples,
                            "channel_count":  channel_count,
                            "chunks":         [None] * chunk_count,
                            "received":       0,
                            "first_seen":     time.monotonic(),
                        }
                        pending[frame_id] = frame

                    if chunk_index < len(frame["chunks"]) and frame["chunks"][chunk_index] is None:
                        frame["chunks"][chunk_index] = payload
                        frame["received"] += 1

                    if frame["received"] == len(frame["chunks"]):
                        assembled = b"".join(frame["chunks"])
                        expected = frame["n_samples"] * frame["channel_count"] * 2
                        if len(assembled) == expected:
                            self._process_payload(
                                assembled,
                                frame["channel_count"],
                                frame["sample_rate_hz"],
                            )
                        del pending[frame_id]

                    self._expire_stale_udp(pending)
        except Exception as exc:
            _log.error("RFSoC UDP receiver error: %s", exc)
        finally:
            self._running = False

    @staticmethod
    def _expire_stale_udp(pending: dict) -> None:
        now = time.monotonic()
        stale = [
            fid for fid, f in pending.items()
            if now - f["first_seen"] > UDP_STALE_TIMEOUT_S
        ]
        for fid in stale:
            del pending[fid]
