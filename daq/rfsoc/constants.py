"""RFSoC ADC stream protocol constants."""

import struct

TCP_HEADER = struct.Struct(">4sHHQQII")
UDP_HEADER = struct.Struct(">4sHHQQIHHII")

TCP_MAGIC_V1 = b"RFT1"
TCP_MAGIC_V2 = b"RFT2"
TCP_MAGIC_V3 = b"RFT3"
UDP_MAGIC_V1 = b"RFU1"
UDP_MAGIC_V2 = b"RFU2"
UDP_MAGIC_V3 = b"RFU3"

CHANNEL_LABELS = ("ADC_D", "ADC_C", "ADC_B", "ADC_A")

UDP_STALE_TIMEOUT_S: float = 2.0
