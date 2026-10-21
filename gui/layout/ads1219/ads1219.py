from __future__ import annotations

import threading
from pathlib import Path

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ADS1219Tab(QWidget):
    def __init__(self):
        super()._init_()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("ADS1219"))
        self.setLayout(layout)
