import multiprocessing as _mp
import os
import signal
import threading
import time

from pixels._log import get_logger
from pixels.daq._base import DAQDevice, SampleBatch
from .constants import PORT_MAP, SCRATCH_FORMAT, UDP_PORT, WRITE_BATCH
from .profiles import ADX134Profile, N_STREAMS, RAW_PROFILE, get_adx134_profile

_log = get_logger(__name__)
from .receiver import BinaryReceiver
from .packet_store import PacketReader, capture_path, normalize_scratch_format


class ADX134DAQ(DAQDevice):
    def __init__(
        self,
        port_map=PORT_MAP,
        udp_port=UDP_PORT,
        bin_dir=".",
        debug=False,
        scratch_format=SCRATCH_FORMAT,
        write_batch=WRITE_BATCH,
        profile: ADX134Profile | str = RAW_PROFILE,
        odr_hz: float | None = None,
    ):
        self.bin_dir = bin_dir
        self._root_bin_dir = bin_dir
        self.port_map = port_map
        self.debug = debug
        self.scratch_format = normalize_scratch_format(scratch_format)
        self.write_batch = write_batch
        self.profile = (
            get_adx134_profile(profile) if isinstance(profile, str) else profile
        )
        self.odr_hz = odr_hz
        # One 10G connection carries all streams: frame k of every stream
        # arrives in one datagram, split on receipt into N_STREAMS files.
        # Only the first connection entry is used.
        self.receivers = [
            BinaryReceiver(
                port_map[0], udp_port, debug=debug, profile=self.profile, odr_hz=odr_hz
            )
        ] if port_map else []
        self.n_ports = N_STREAMS
        self._running = False

    # ── DAQDevice protocol ───────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def n_samples(self) -> int:
        return self.live_reader.n_samples

    @property
    def sample_rate(self) -> float:
        return self.live_reader.sample_rate

    @property
    def n_channels(self) -> int:
        return self.live_reader.n_channels

    @property
    def channel_labels(self) -> list[str]:
        return self.live_reader.channel_labels

    @property
    def device_metadata(self) -> dict:
        return {
            "n_ports": self.n_ports,
            "bin_dir": self.bin_dir,
            "scratch_format": self.scratch_format,
            **self.profile.attrs(self.odr_hz),
        }

    @property
    def live_reader(self):
        """DataStreamBase backed by the current scratch files."""
        from ._reader import ADX134LiveReader

        return ADX134LiveReader(self)

    def get_latest(self, n: int) -> SampleBatch:
        return self.live_reader.get_latest(n)

    def get_range(self, start: int, stop: int) -> SampleBatch:
        return self.live_reader.get_range(start, stop)

    def configure(self, config: dict) -> None:
        """Update internal capture parameters from config dict.

        Reads adx134.capture.* and adx134.clock_gen.* keys.  Hardware-side
        configuration (ODR, demod mode sent over UDP) is handled separately
        by the control layer.
        """
        from pixels.daq.adx134.profiles import profile_from_config

        adx = config.get("adx134", {})
        cap = adx.get("capture", {})
        if cap.get("scratch_format"):
            self.scratch_format = normalize_scratch_format(cap["scratch_format"])
        if cap.get("write_batch"):
            self.write_batch = int(cap["write_batch"])
        if cap.get("debug") is not None:
            self.debug = bool(cap["debug"])
        self.profile = profile_from_config(config)

    # ── Existing API ──────────────────────────────────────────────────────────

    def bin_path(self, port_idx: int) -> str:
        return self.capture_path(port_idx)

    def capture_path(self, port_idx: int) -> str:
        return capture_path(self.bin_dir, port_idx, self.scratch_format)

    def stream_paths(self) -> list[str]:
        return [self.capture_path(i) for i in range(self.n_ports)]

    def _require_receiver(self) -> BinaryReceiver:
        if not self.receivers:
            raise RuntimeError("No ADX134 connection configured (connections.adx134.eth0)")
        return self.receivers[0]

    def _log_capture_plan(self) -> None:
        r = self._require_receiver()
        _log.info(
            "Capturing to %d %s files in '%s/'",
            self.n_ports,
            self.scratch_format,
            self.bin_dir,
        )
        _log.info("  Link: %s:%d <- %s", r.host_ip, r.udp_port, r.remote_ip)
        for i in range(self.n_ports):
            _log.info("  Port %d  ->  %s", i, self.bin_path(i))

    def connect_sockets(self) -> None:
        """Bind the UDP socket. Call this before tx_enable so no packets are missed."""
        self._require_receiver().connect()
        self._log_capture_plan()

    def start_threads(self, pin_cpu: bool = False) -> None:
        """Start the receiver thread. The socket must already be connected (threading path)."""
        r = self._require_receiver()
        self._stop_event = threading.Event()
        self._start_time = time.time()
        self._pkt_counters = None
        self._threads = [
            threading.Thread(
                target=r.receiver_thread,
                args=(
                    self._stop_event,
                    self.stream_paths(),
                    0 if pin_cpu else -1,
                    self.scratch_format,
                    self.write_batch,
                ),
                daemon=True,
                name="recv-link",
            )
        ]
        for t in self._threads:
            t.start()

    def _start_processes(self) -> None:
        """Start the receiver child process — own GIL, no Qt contention."""
        import logging
        from logging.handlers import QueueListener
        from .receiver import _process_receiver

        r = self._require_receiver()
        self._stop_event = _mp.Event()
        self._pkt_counters = [_mp.Value("L", 0, lock=False)]
        self._ready_events = [_mp.Event()]

        # Relay log records from the subprocess back to the main-process 'pixels' logger
        self._log_queue = _mp.Queue()
        self._log_listener = QueueListener(
            self._log_queue,
            *logging.getLogger("pixels").handlers or [logging.NullHandler()],
            respect_handler_level=True,
        )
        self._log_listener.start()

        self._threads = [
            _mp.Process(
                target=_process_receiver,
                args=(
                    r.remote_ip,
                    r.host_ip,
                    r.udp_port,
                    self._stop_event,
                    self.stream_paths(),
                    0,
                    self._pkt_counters[0],
                    self.debug,
                    self.scratch_format,
                    self.write_batch,
                    self.profile,
                    self.odr_hz,
                    self._ready_events[0],
                ),
                kwargs={"log_queue": self._log_queue},
                daemon=True,
                name="recv-link",
            )
        ]
        self._log_capture_plan()
        for p in self._threads:
            p.start()
        self._start_time = time.time()

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """Block until the receiver process has bound its socket. Call before tx_enable."""
        if not hasattr(self, "_ready_events"):
            return True
        for ev in self._ready_events:
            if not ev.wait(timeout):
                return False
        self._start_time = time.time()
        return True

    def start(self, save_path: str | None = None, use_processes: bool = False) -> None:
        """Connect the socket and start the receiver. Use use_processes=True from GUI."""
        if save_path is not None:
            self.bin_dir = save_path
        else:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.bin_dir = os.path.join(self._root_bin_dir, ts)
        os.makedirs(self.bin_dir, exist_ok=True)
        self._running = True
        if use_processes:
            self._start_processes()
        else:
            self.connect_sockets()
            self.start_threads(pin_cpu=True)

    def _log_stats(self, n_datagrams: int, duration: float) -> None:
        r = self._require_receiver()
        _log.info("--- Capture stats ---")
        _log.info("  Link (%s): datagrams=%s", r.remote_ip, f"{n_datagrams:,}")
        for i in range(self.n_ports):
            p = self.bin_path(i)
            sz = os.path.getsize(p) if os.path.exists(p) else 0
            mbps = (
                (n_datagrams * self.profile.payload_bytes / duration) / 1e6
                if duration > 0
                else 0
            )
            _log.info(
                "  Port %d: pkts=%s, file=%.1f MB, %.2f MB/s",
                i,
                f"{n_datagrams:,}",
                sz / 1e6,
                mbps,
            )
        _log.info("  Duration: %.2f s", duration)

    def stop(self) -> None:
        """Signal the receiver to stop, join it, and print stats."""
        self._running = False
        if not hasattr(self, "_stop_event"):
            return
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=10)

        if hasattr(self, "_log_listener"):
            self._log_listener.stop()

        pkt_counters = self._pkt_counters
        if pkt_counters is None:
            for r in self.receivers:
                r.disconnect()
            n = self.receivers[0].pkts_received if self.receivers else 0
        else:
            n = pkt_counters[0].value

        self._log_stats(n, time.time() - self._start_time)

    def run(self):
        from pixels.control.comms import Connection
        from pixels.control.board import tx_enable

        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit))

        os.makedirs(self.bin_dir, exist_ok=True)
        r = self._require_receiver()
        r.connect()
        conn = Connection(fpga_ip=r.remote_ip, fpga_port=r.udp_port, host_ip=r.host_ip)

        stop_event = threading.Event()
        thread = threading.Thread(
            target=r.receiver_thread,
            args=(
                stop_event,
                self.stream_paths(),
                0,
                self.scratch_format,
                self.write_batch,
            ),
            daemon=True,
            name="recv-link",
        )
        thread.start()

        self._log_capture_plan()
        _log.info("Press Ctrl-C to stop.")

        tx_enable(True, conn)

        start_time = time.time()
        try:
            while True:
                time.sleep(2.0)
                elapsed = time.time() - start_time
                parts = []
                for i in range(self.n_ports):
                    p = self.bin_path(i)
                    pkts = 0
                    if os.path.exists(p):
                        try:
                            with PacketReader(
                                p,
                                swmr=p.endswith((".h5", ".hdf5")),
                                profile=self.profile,
                            ) as reader:
                                reader.refresh()
                                pkts = reader.n_packets
                        except (OSError, KeyError, ValueError):
                            pkts = 0
                    parts.append(f"p{i}:{pkts:,}pkts")
                _log.debug("  [%6.0fs] %s", elapsed, " | ".join(parts))

        except (KeyboardInterrupt, SystemExit):
            _log.info("Interrupted by user")

        finally:
            tx_enable(False, conn)
            stop_event.set()
            thread.join(timeout=10)
            r.disconnect()
            self._log_stats(r.pkts_received, time.time() - start_time)
