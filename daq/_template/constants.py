"""
Template: device constants.
Copy to src/pixels/daq/<device>/constants.py and replace every TODO.
See docs/gui/updates.md for the full integration checklist.
"""

# TODO: replace with actual values

SAMPLE_RATE: float = 0.0        # Hz
N_CHANNELS:  int   = 0          # total ADC channels

V_REF:       float = 0.0        # reference voltage, volts
RESOLUTION:  int   = 24         # ADC bit depth
LSB:         float = V_REF * 2 / (2 ** RESOLUTION)

# Channel labels — one string per channel index
CHANNEL_LABELS: list[str] = [f"ch{i}" for i in range(N_CHANNELS)]
