"""Serial sample parsing types and regex patterns for the ADS1219 firmware protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import LSB

__all__ = [
    "ADCSample",
    "_SAMPLE_RE", "_REG_RE", "_DUMP_RE", "_RDY_RE", "_OK_RE",
]

# ── Regex patterns ────────────────────────────────────────────────────────────

_SAMPLE_RE = re.compile(r"^ads1219:\s+(\d+)\s+(-?\d+)")  # mux, raw
_REG_RE    = re.compile(r"^reg\s+(\d+)\s+(0x[0-9a-fA-F]+)")
_DUMP_RE   = re.compile(r"^ADS1219 (config|status)\s+=\s+(0x[0-9a-fA-F]+)")
_RDY_RE    = re.compile(r"^rdy\s+([01])")
_OK_RE     = re.compile(r"^ok")


# ── Sample datatype ───────────────────────────────────────────────────────────

@dataclass
class ADCSample:
    raw:       int
    channel:   int    # MUX field value active at time of sample
    timestamp: float

    @property
    def voltage(self) -> float:
        return self.raw * LSB

    @property
    def millivolts(self) -> float:
        return self.voltage * 1000.0
