"""
Firmware command protocol:
  stream on / stream off          — start/stop continuous sampling
  reg rd <n>                      — read register n   -> "reg <n> 0x<hex>"
  reg wr <n> <hex>                — write register    -> "ok"
  reg dump                        — dump config+status -> "config 0x<hex>" / "status 0x<hex>"
  cmd <hex>                       — send raw ADS1219 command byte -> "ok"
  set mux|gain|rate|mode|vref <n> — modify config field -> "ok"
  rdy                             — check DRDY status -> "rdy <0|1>"
  Streaming sample:               "ads1219: <mux> <raw>"
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import numpy as np

from pixels._log import get_logger
from pixels.control.comms.serial import SerialConnection
from pixels.daq._base import DAQDevice, SampleBatch
from .constants import DR_LABELS, LSB, MUX_LABELS, V_REF
from .receiver import (
    ADCSample,
    _DUMP_RE,
    _OK_RE,
    _REG_RE,
    _RDY_RE,
    _SAMPLE_RE,
)
from .packet_store import save_hdf5

_log = get_logger(__name__)


# ── Config / register dataclasses ─────────────────────────────────────────────


@dataclass
class ADS1219Config:
    address: int = 0x40
    mux: int = 0  # MUX field (0-7), see MUX_LABELS
    gain: int = 0  # GAIN field (0=1x, 1=4x)
    data_rate: int = 0  # DR field (0=20SPS … 3=1000SPS)
    conversion_mode: int = 0  # CM field (0=single-shot, 1=continuous)
    vref: int = 0  # VREF field (0=internal 2.048V, 1=external)

    def to_byte(self) -> int:
        return (
            (self.mux & 0x7) << 5
            | (self.gain & 0x1) << 4
            | (self.data_rate & 0x3) << 2
            | (self.conversion_mode & 0x1) << 1
            | (self.vref & 0x1)
        )


@dataclass
class ADCRegisters:
    config_raw: int
    mux: int
    gain: int
    data_rate: int
    continuous: bool
    ext_vref: bool
    status_raw: int
    drdy: bool

    @property
    def mux_label(self) -> str:
        return MUX_LABELS.get(self.mux, f"unknown({self.mux})")

    @property
    def data_rate_label(self) -> str:
        return DR_LABELS.get(self.data_rate, f"unknown({self.data_rate})")


# ── Main class ────────────────────────────────────────────────────────────────


class ADS1219(DAQDevice):
    """
    Controls the ADS1219 via the firmware UART command interface.

    Parameters
    ----------
    port : str
        Serial port device (e.g. "/dev/ttyUSB1").
    baud : int
        Baud rate, must match firmware (default 115200).
    config : ADS1219Config, optional
        Initial device configuration applied by init().
    verbose : bool
        If True, log each received sample at DEBUG level.
    sample_callback : callable, optional
        Called with each ADCSample as it arrives during streaming.
    max_samples : int
        Ring buffer capacity; oldest samples evicted when full.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        config: Optional[ADS1219Config] = None,
        verbose: bool = False,
        sample_callback: Optional[Callable[[ADCSample], None]] = None,
        max_samples: int = 100_000,
    ):
        self._conn = SerialConnection(port, baud, echo=False)
        self._config = config or ADS1219Config()
        self._verbose = verbose
        self._sample_queue: queue.Queue[ADCSample] = queue.Queue()
        self._extra_handler: Optional[Callable[[str], None]] = None
        self._running = False
        self._parse_thread: Optional[threading.Thread] = None
        self._current_mux = self._config.mux
        self._save_path: Optional[str] = None
        self._max_samples = max_samples
        # Per-channel ring buffers: mux_value -> (timestamps deque, raw deque)
        self._buf: dict[int, tuple[deque, deque]] = {}

        user_cb = sample_callback

        def _buf_cb(s: ADCSample) -> None:
            ch = s.channel
            if ch not in self._buf:
                self._buf[ch] = (deque(maxlen=max_samples), deque(maxlen=max_samples))
            self._buf[ch][0].append(s.timestamp)
            self._buf[ch][1].append(s.raw)
            if user_cb:
                user_cb(s)

        self._sample_callback = _buf_cb

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        self._conn.connect()
        self._running = True
        self._parse_thread = threading.Thread(
            target=self._parse_loop, daemon=True, name="ads1219-parser"
        )
        self._parse_thread.start()

    def disconnect(self) -> None:
        self.stop()
        self._running = False
        self._conn.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._conn.is_connected

    # ── Device init ───────────────────────────────────────────────────────────

    def init(self) -> None:
        """Reset device then write full config register from self._config."""
        self.send_cmd(0x06)  # RESET
        time.sleep(0.01)
        self.reg_wr(0, self._config.to_byte())

    # ── Register access ───────────────────────────────────────────────────────

    def reg_dump(self, timeout: float = 2.0) -> Optional[ADCRegisters]:
        """Read config and status registers. Returns ADCRegisters or None on timeout."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            m = _DUMP_RE.match(line)
            if m:
                result_q.put((m.group(1), int(m.group(2), 16)))

        self._extra_handler = _capture
        self._conn.send("i2c")

        gathered: dict = {}
        deadline = time.time() + timeout
        while time.time() < deadline and len(gathered) < 2:
            try:
                key, val = result_q.get(timeout=0.1)
                gathered[key] = val
            except queue.Empty:
                continue
        self._extra_handler = None

        if "config" not in gathered or "status" not in gathered:
            return None

        c, s = gathered["config"], gathered["status"]
        return ADCRegisters(
            config_raw=c,
            mux=(c >> 5) & 0x7,
            gain=(c >> 4) & 0x1,
            data_rate=(c >> 2) & 0x3,
            continuous=bool((c >> 1) & 0x1),
            ext_vref=bool(c & 0x1),
            status_raw=s,
            drdy=bool((s >> 7) & 0x1),
        )

    def reg_rd(self, reg: int, timeout: float = 1.0) -> Optional[int]:
        """Read a single register. Returns the value as int, or None on timeout."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            m = _REG_RE.match(line)
            if m and int(m.group(1)) == reg:
                result_q.put(int(m.group(2), 16))

        self._extra_handler = _capture
        self._conn.send(f"reg rd {reg}")
        try:
            return result_q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            self._extra_handler = None

    def reg_wr(self, reg: int, val: int, timeout: float = 1.0) -> bool:
        """Write a register. Returns True on acknowledged, False on timeout."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            if _OK_RE.match(line):
                result_q.put(True)

        self._extra_handler = _capture
        self._conn.send(f"reg wr {reg} {val:#04x}")
        try:
            result_q.get(timeout=timeout)
            return True
        except queue.Empty:
            return False
        finally:
            self._extra_handler = None

    def send_cmd(self, cmd: int, timeout: float = 1.0) -> bool:
        """Send a raw ADS1219 command byte. Returns True on acknowledged."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            if _OK_RE.match(line):
                result_q.put(True)

        self._extra_handler = _capture
        self._conn.send(f"cmd {cmd:#04x}")
        try:
            result_q.get(timeout=timeout)
            return True
        except queue.Empty:
            return False
        finally:
            self._extra_handler = None

    # ── Config helpers ────────────────────────────────────────────────────────

    def _send_set(self, field: str, val: int, timeout: float = 1.0) -> bool:
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            if _OK_RE.match(line):
                result_q.put(True)

        self._extra_handler = _capture
        self._conn.send(f"set {field} {val}")
        try:
            result_q.get(timeout=timeout)
            return True
        except queue.Empty:
            return False
        finally:
            self._extra_handler = None

    def set_ch_cfg(self, mux: int) -> bool:
        """Set MUX field (0-7). Updates internal channel tracking."""
        self._current_mux = mux
        return self._send_set("mux", mux)

    def set_gain(self, gain: int) -> bool:
        """Set GAIN field (0=1x, 1=4x)."""
        return self._send_set("gain", gain)

    def set_conversion_mode(self, mode: int) -> bool:
        """Set CM field (0=single-shot, 1=continuous)."""
        return self._send_set("mode", mode)

    def set_vref(self, vref: int) -> bool:
        """Set VREF field (0=internal 2.048V, 1=external)."""
        return self._send_set("vref", vref)

    def set_channels(self, mask: int, timeout: float = 1.0) -> bool:
        """Set channel scan mask (bits 0-6 = MUX values to cycle). Sends 'ads1219 ch <hex>'."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            if _OK_RE.match(line):
                result_q.put(True)

        self._extra_handler = _capture
        self._conn.send(f"ads1219 ch {mask:#04x}")
        try:
            result_q.get(timeout=timeout)
            return True
        except queue.Empty:
            return False
        finally:
            self._extra_handler = None

    # ── DRDY ──────────────────────────────────────────────────────────────────

    def is_rdy(self, timeout: float = 1.0) -> Optional[bool]:
        """Query DRDY status from firmware. Returns True/False or None on timeout."""
        result_q: queue.Queue = queue.Queue()

        def _capture(line: str) -> None:
            m = _RDY_RE.match(line)
            if m:
                result_q.put(bool(int(m.group(1))))

        self._extra_handler = _capture
        self._conn.send("rdy")
        try:
            return result_q.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            self._extra_handler = None

    def wait_rdy(self, timeout: float = 5.0) -> bool:
        """Block until DRDY is asserted or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rdy = self.is_rdy(timeout=min(0.5, deadline - time.time()))
            if rdy:
                return True
            time.sleep(0.01)
        return False

    # ── Streaming ─────────────────────────────────────────────────────────────

    def start(self, save_path: Optional[str] = None) -> None:
        """Begin continuous streaming. If save_path is given, auto-saves to HDF5 on stop()."""
        self._save_path = save_path
        self._conn.send("ads1219 print on")
        self._conn.send("ads1219 on")

    def stop(self) -> None:
        """Stop streaming and auto-save if a save_path was set."""
        if self.is_connected:
            self._conn.send("ads1219 print off")
            self._conn.send("ads1219 off")
        if self._save_path and self.n_samples > 0:
            self.save(self._save_path)
            self._save_path = None

    def read_data(
        self, count: Optional[int] = None, timeout: float = 5.0
    ) -> Iterator[ADCSample]:
        """Iterate over streaming samples. count=None runs until stop() / timeout."""
        n = 0
        while count is None or n < count:
            try:
                sample = self._sample_queue.get(timeout=timeout)
                yield sample
                n += 1
            except queue.Empty:
                return

    def get_sample(self, timeout: float = 5.0) -> Optional[ADCSample]:
        """Return the next streaming sample, or None if timeout expires."""
        try:
            return self._sample_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ── DAQDevice protocol ────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def sample_rate(self) -> float:
        return {0: 20.0, 1: 90.0, 2: 330.0, 3: 1000.0}.get(self._config.data_rate, 0.0)

    @property
    def n_channels(self) -> int:
        return len(self._buf) if self._buf else 1

    @property
    def channel_labels(self) -> list[str]:
        if self._buf:
            return [MUX_LABELS.get(m, f"MUX{m}") for m in sorted(self._buf)]
        return [MUX_LABELS.get(self._config.mux, f"MUX{self._config.mux}")]

    @property
    def device_metadata(self) -> dict:
        return {
            "gain": self._config.gain,
            "data_rate": self._config.data_rate,
            "sample_rate_hz": self.sample_rate,
            "active_channels": self.active_channels,
        }

    def get_latest(self, n: int) -> SampleBatch:
        """Return most recent n samples as a SampleBatch (all channels merged by time)."""
        channels = sorted(self._buf.keys())
        if not channels:
            return SampleBatch(
                data=np.empty((0, 1)),
                time=np.empty(0),
                channel_labels=self.channel_labels,
                units="V",
                sample_rate=self.sample_rate,
            )
        min_n = min(len(self._buf[m][1]) for m in channels)
        use_n = min(n, min_n)
        per_ch, per_t = [], []
        for m in channels:
            t_d, r_d = self._buf[m]
            t_arr = np.array(list(t_d)[-use_n:], dtype=np.float64)
            r_arr = np.array(list(r_d)[-use_n:], dtype=np.int32)
            per_ch.append(r_arr.astype(np.float64) * LSB)
            per_t.append(t_arr)
        data = np.column_stack(per_ch) if per_ch else np.empty((0, len(channels)))
        t = per_t[0] - per_t[0][0] if per_t else np.empty(0)
        return SampleBatch(
            data=data,
            time=t,
            channel_labels=[MUX_LABELS.get(m, f"MUX{m}") for m in channels],
            units="V",
            sample_rate=self.sample_rate,
            metadata=self.device_metadata,
        )

    def get_range(self, start: int, stop: int) -> SampleBatch:
        """Return samples [start, stop) by global sample index (across all channels)."""
        return self.get_latest(stop - start)

    def configure(self, config: dict) -> None:
        """Update config and push to hardware if already connected.

        Reads ads1219.gain, ads1219.data_rate, ads1219.channel_mask keys.
        If already connected, calls init() and set_channels() to apply.
        """
        ads = config.get("ads1219", {})
        self._config = ADS1219Config(
            gain=int(ads.get("gain", self._config.gain)),
            data_rate=int(ads.get("data_rate", self._config.data_rate)),
        )
        if self.is_connected:
            self.init()
            mask = int(ads.get("channel_mask", 0x03))
            self.set_channels(mask)

    # ── Additional API ────────────────────────────────────────────────────────

    @property
    def n_samples(self) -> int:
        return sum(len(v[1]) for v in self._buf.values())

    @property
    def active_channels(self) -> list[int]:
        """MUX values that have received at least one sample."""
        return sorted(self._buf.keys())

    def get_samples(self, mux: Optional[int] = None) -> tuple[np.ndarray, np.ndarray]:
        """Returns (timestamps_s, raw_int32) for one channel or all combined."""
        if mux is not None:
            t_d, r_d = self._buf.get(mux, (deque(), deque()))
            return np.array(t_d, dtype=np.float64), np.array(r_d, dtype=np.int32)
        all_t, all_r = [], []
        for t_d, r_d in self._buf.values():
            all_t.extend(t_d)
            all_r.extend(r_d)
        if not all_t:
            return np.array([], dtype=np.float64), np.array([], dtype=np.int32)
        idx = np.argsort(all_t)
        return (
            np.array(all_t, dtype=np.float64)[idx],
            np.array(all_r, dtype=np.int32)[idx],
        )

    def get_voltages(self, mux: Optional[int] = None) -> tuple[np.ndarray, np.ndarray]:
        """Returns (timestamps_s, volts) for one channel or all combined."""
        t, raw = self.get_samples(mux)
        return t, raw.astype(np.float64) * LSB

    def clear(self) -> None:
        self._buf.clear()

    def save(self, path: str) -> None:
        """Write per-channel buffers to HDF5."""
        save_hdf5(self._buf, path)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _parse_loop(self) -> None:
        while self._running:
            try:
                line = self._conn.rx_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            m = _SAMPLE_RE.match(line)
            if m:
                sample = ADCSample(
                    raw=int(m.group(2)),
                    channel=int(m.group(1)),
                    timestamp=time.time(),
                )
                self._sample_queue.put(sample)
                if self._verbose:
                    _log.debug("smpl %d  (%.3f mV)", sample.raw, sample.millivolts)
                if self._sample_callback:
                    self._sample_callback(sample)

            handler = self._extra_handler
            if handler:
                handler(line)

    def __enter__(self) -> "ADS1219":
        self.connect()
        self.init()
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
        self.disconnect()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="ADS1219 test")
    parser.add_argument("--port", default="/dev/ttyUSB1")
    parser.add_argument("--baud", default=115200, type=int)
    parser.add_argument("--samples", default=20, type=int)
    args = parser.parse_args()

    print(f"Connecting to {args.port} @ {args.baud} baud...")
    try:
        with ADS1219(args.port, args.baud, verbose=True) as adc:
            regs = adc.reg_dump()
            if regs:
                print(
                    f"config=0x{regs.config_raw:02x}  MUX={regs.mux_label}  "
                    f"GAIN={regs.gain}x  DR={regs.data_rate_label}  "
                    f"CM={'continuous' if regs.continuous else 'single-shot'}  "
                    f"VREF={'ext' if regs.ext_vref else 'int'}"
                )
                print(f"status=0x{regs.status_raw:02x}  DRDY={regs.drdy}")
            else:
                print("reg_dump timed out")

            print(f"Collecting {args.samples} samples...")
            samples_mv = []
            for s in adc.read_data(count=args.samples, timeout=10.0):
                print(f"  raw={s.raw:8d}  {s.millivolts:8.3f} mV  ch={s.channel}")
                samples_mv.append(s.millivolts)
            if samples_mv:
                print(f"mean: {np.mean(samples_mv):.3f} mV")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
