import logging
import sys
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont


class QtLoggingHandler(logging.Handler, QObject):
    """Logging handler that emits each formatted record as a Qt signal."""
    record_emitted = Signal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter(
            "%(levelname)-8s %(name)s — %(message)s"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.record_emitted.emit(self.format(record))
        except Exception:
            pass


class Console(QWidget):
    def __init__(self):
        super().__init__()
        self._handler: QtLoggingHandler | None = None
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Monospace", 9))
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(self.output.clear)
        hdr = QHBoxLayout()
        hdr.addStretch()
        hdr.addWidget(clear_btn)
        layout.addLayout(hdr)
        layout.addWidget(self.output)
        self.setLayout(layout)

    def install_logging(self, level: int = logging.DEBUG) -> None:
        """Attach a logging handler to the 'pixels' logger and show records here."""
        if self._handler is None:
            self._handler = QtLoggingHandler()
            self._handler.record_emitted.connect(self.output.append)
        pkg_logger = logging.getLogger("pixels")
        pkg_logger.setLevel(level)
        if self._handler not in pkg_logger.handlers:
            pkg_logger.addHandler(self._handler)

    def uninstall_logging(self) -> None:
        """Detach the handler; 'pixels' logger goes back to NullHandler (silent)."""
        if self._handler is not None:
            logging.getLogger("pixels").removeHandler(self._handler)
        logging.getLogger("pixels").setLevel(logging.WARNING)


class BottomPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.console = Console()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.console)
        self.setLayout(layout)
