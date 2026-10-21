"""
Template: protocol data types and sample parsing.
Copy to src/pixels/daq/<device>/receiver.py and replace every TODO.
See docs/gui/updates.md for the full integration checklist.

For serial/text-based protocols: define regex patterns and a Sample dataclass here.
For binary/UDP protocols: define a receiver class with connect/disconnect/receiver_thread.
"""

from __future__ import annotations

from dataclasses import dataclass

# TODO: import the LSB constant from .constants once values are filled in
# from .constants import LSB


# ── Sample datatype ───────────────────────────────────────────────────────────

@dataclass
class Sample:
    """One acquired sample from the device."""
    raw:       int      # TODO: adjust fields to match device output
    channel:   int
    timestamp: float

    @property
    def voltage(self) -> float:
        # TODO: return self.raw * LSB
        raise NotImplementedError

    @property
    def millivolts(self) -> float:
        return self.voltage * 1000.0


# ── Protocol parsing ──────────────────────────────────────────────────────────
# TODO: add regex patterns (serial) or receiver class (UDP/binary) here.
#
# Serial example:
#   import re
#   _SAMPLE_RE = re.compile(r"^data:\s+(\d+)\s+(-?\d+)")
#
# UDP/binary example: see src/pixels/daq/adx134/receiver.py for a BinaryReceiver
# class with connect(), disconnect(), and receiver_thread().
