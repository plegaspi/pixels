SINGLE_ENDED_MASK:  int = 0x78  # MUX 3,4,5,6 — AINx vs AGND
DIFFERENTIAL_MASK:  int = 0x03  # MUX 0,1     — AIN0-1, AIN2-3
ALL_CHANNELS_MASK:  int = 0x7F  # MUX 0-6

V_REF: float = 2.048       # internal reference, volts
MAX_CODE: int = 8_388_607  # 2^23 - 1
LSB: float = V_REF / MAX_CODE

MUX_LABELS: dict[int, str] = {
    0: "AIN0-AIN1",
    1: "AIN2-AIN3",
    2: "AIN1-AIN2",
    3: "AIN0-AGND",
    4: "AIN1-AGND",
    5: "AIN2-AGND",
    6: "AIN3-AGND",
    7: "SHORT (AVDD/2)",
}

DR_LABELS: dict[int, str] = {
    0: "20 SPS",
    1: "90 SPS",
    2: "330 SPS",
    3: "1000 SPS",
}
