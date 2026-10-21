import contextlib
import logging
import os
import socket
import threading
import time

from .packet_store import PacketSpoolWriter
from .profiles import ADX134Profile, MAX_DATAGRAM_BYTES, N_STREAMS, RAW_PROFILE


def _configure_subprocess_logging(log_queue) -> None:
    """Install a QueueHandler so subprocess log records are relayed to the main process."""
    if log_queue is None:
        return
    from logging.handlers import QueueHandler
    root = logging.getLogger("pixels")
    root.handlers.clear()
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(logging.DEBUG)


def _pin_to_cpu(cpu: int) -> None:
    """Pin the calling process to one CPU where supported (Linux only; no-op elsewhere)."""
    if not hasattr(os, "sched_setaffinity"):
        return
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError:
        pass


class _SeqCheck:
    """Counts datagrams whose sub-frames disagree on frame_seq (rate-limited log)."""

    def __init__(self, log: logging.Logger, tag: str):
        self._log = log
        self._tag = tag
        self.mismatches = 0
        self._last_log = 0.0

    def check(self, data, payload_bytes: int) -> None:
        seq0 = data[0]
        for i in range(1, N_STREAMS):
            if data[i * payload_bytes] != seq0:
                self.mismatches += 1
                now = time.monotonic()
                if now - self._last_log >= 1.0:
                    self._last_log = now
                    self._log.warning(
                        "[%s] sub-frame frame_seq mismatch %s (%d so far)",
                        self._tag,
                        [data[j * payload_bytes] for j in range(N_STREAMS)],
                        self.mismatches,
                    )
                return


def _open_writers(stack, bin_paths, scratch_format, write_batch, attrs, profile):
    return [
        stack.enter_context(
            PacketSpoolWriter(
                path,
                scratch_format=scratch_format,
                chunk_packets=write_batch,
                attrs={**attrs, "stream": i},
                profile=profile,
            )
        )
        for i, path in enumerate(bin_paths)
    ]


def _receive_loop(sock, writers, profile, stop_event, remote_ip, log, on_count):
    """Receive datagrams, split each into N_STREAMS sub-frames, write one per file.

    A datagram is written to all files or to none, so the per-stream files
    always hold the same number of packets and stay aligned.
    """
    payload = profile.payload_bytes
    expected = profile.datagram_bytes
    tag = f"{remote_ip}:{sock.getsockname()[1]}"
    seq = _SeqCheck(log, tag)
    bad_len = 0
    last_bad_log = 0.0
    n = 0
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(MAX_DATAGRAM_BYTES + 64)
        except (TimeoutError, socket.timeout):
            continue
        except ConnectionResetError:
            # Windows reports an earlier ICMP port-unreachable on the next
            # recvfrom (WSAECONNRESET); it does not close the socket.
            continue
        except OSError:
            log.exception("[%s] recvfrom failed", tag)
            break
        if addr[0] != remote_ip:
            continue
        if len(data) != expected:
            bad_len += 1
            now = time.monotonic()
            if now - last_bad_log >= 1.0:
                last_bad_log = now
                log.warning("[%s] Bad datagram length %d (expected %d), skipping (%d so far)",
                            tag, len(data), expected, bad_len)
            continue
        seq.check(data, payload)
        mv = memoryview(data)
        for i, w in enumerate(writers):
            w.write(mv[i * payload:(i + 1) * payload])
        n += 1
        on_count(n)
    if seq.mismatches:
        log.warning("[%s] %d datagram(s) with mismatched sub-frame frame_seq", tag, seq.mismatches)
    if bad_len:
        log.warning("[%s] %d datagram(s) with unexpected length skipped", tag, bad_len)
    return n


def _process_receiver(*args, **kwargs) -> None:
    """Child-process entry point: run the receiver and report any startup failure.

    Errors in the child would otherwise go only to its stderr, which the GUI
    never shows; the parent would just see the ready event time out.
    """
    try:
        _process_receiver_main(*args, **kwargs)
    except Exception:
        logging.getLogger("pixels.daq.adx134.receiver").exception(
            "Receiver process failed (remote %s, port %s)",
            args[0] if args else "?", args[2] if len(args) > 2 else "?",
        )
        raise


def _process_receiver_main(
    remote_ip: str,
    host_ip: str,
    udp_port: int,
    stop_event,
    bin_paths: list[str],
    cpu: int,
    pkts_counter,
    debug: bool,
    scratch_format: str,
    write_batch: int,
    profile: ADX134Profile = RAW_PROFILE,
    odr_hz: float | None = None,
    ready_event=None,
    log_queue=None,
) -> None:
    """Receiver loop run in a child process — has its own GIL, no Qt contention.

    One socket receives every datagram; each is split into the N_STREAMS
    per-stream files in bin_paths.
    """
    _configure_subprocess_logging(log_queue)
    _log = logging.getLogger("pixels.daq.adx134.receiver")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
    sock.settimeout(1.0)
    sock.bind((host_ip, udp_port))

    actual = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    _log.debug("[%s] Listening on %s:%d, SO_RCVBUF=%d KB",
               remote_ip, host_ip, udp_port, actual // 1024)

    # The socket is bound: datagrams now queue in its receive buffer, so the
    # parent may enable TX.  Opening the scratch file can be slow (HDF5 on
    # Windows), so it happens after this signal rather than before it.
    if ready_event is not None:
        ready_event.set()

    if cpu >= 0:
        _pin_to_cpu(cpu)

    attrs = {
        "remote_ip": remote_ip,
        "host_ip": host_ip,
        "udp_port": udp_port,
        "odr_hz": odr_hz or 0.0,
    }

    def on_count(n):
        if not n & 0xFF:
            pkts_counter.value = n

    t_open = time.monotonic()
    with contextlib.ExitStack() as stack:
        writers = _open_writers(stack, bin_paths, scratch_format, write_batch, attrs, profile)
        _log.debug("[%s:%d] Opened %d stream files in %.2f s",
                   remote_ip, udp_port, len(writers), time.monotonic() - t_open)
        n = _receive_loop(sock, writers, profile, stop_event, remote_ip, _log, on_count)

    pkts_counter.value = n
    sock.close()


class BinaryReceiver:
    def __init__(
        self,
        cfg: dict,
        udp_port: int,
        debug: bool = False,
        profile: ADX134Profile = RAW_PROFILE,
        odr_hz: float | None = None,
    ):
        self.remote_ip = cfg["remote_ip"]
        self.host_ip = cfg["host_ip"]
        self.udp_port = cfg.get("port", udp_port)
        self.debug = debug
        self.profile = profile
        self.odr_hz = odr_hz
        self.socket: socket.socket | None = None
        self.pkts_received = 0

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
        self.socket.settimeout(1.0)
        self.socket.bind((self.host_ip, self.udp_port))

    def disconnect(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    def receiver_thread(
        self,
        stop_event: threading.Event,
        bin_paths: list[str],
        cpu: int = -1,
        scratch_format: str = "bin",
        write_batch: int = 512,
    ):
        _log = logging.getLogger("pixels.daq.adx134.receiver")
        if cpu >= 0:
            _pin_to_cpu(cpu)
        attrs = {
            "remote_ip": self.remote_ip,
            "host_ip": self.host_ip,
            "udp_port": self.udp_port,
            "odr_hz": self.odr_hz or 0.0,
        }

        def on_count(n):
            self.pkts_received = n

        with contextlib.ExitStack() as stack:
            writers = _open_writers(stack, bin_paths, scratch_format, write_batch,
                                    attrs, self.profile)
            _receive_loop(self.socket, writers, self.profile, stop_event,
                          self.remote_ip, _log, on_count)
