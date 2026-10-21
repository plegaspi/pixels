import queue
import threading

DEFAULT_PORT = "/dev/ttyUSB3"
DEFAULT_BAUD = 115200


class SerialConnection:
    """
    Wraps a pyserial port with a background reader thread.

    Received lines are placed in `rx_queue` (str items).
    Local echo mirrors sent text back into rx_queue, matching picocom --echo.
    """

    def __init__(
        self, port: str = DEFAULT_PORT, baud: int = DEFAULT_BAUD, echo: bool = True
    ):
        self._port = port
        self._baud = baud
        self._echo = echo
        self._serial = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.rx_queue: queue.Queue[str] = queue.Queue()

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import serial

        self._serial = serial.Serial(self._port, self._baud, timeout=0.1)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Send ──────────────────────────────────────────────────────────────────

    def send(self, text: str) -> None:
        if not self.is_connected:
            raise RuntimeError("Not connected")
        self._serial.write((text + "\r\n").encode())

    # ── Reader thread ─────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        while self._running and self._serial and self._serial.is_open:
            try:
                raw = self._serial.readline()
                if raw:
                    self.rx_queue.put(raw.decode(errors="replace").rstrip("\r\n"))
            except Exception:
                self._running = False
