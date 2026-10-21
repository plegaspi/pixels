"""
High-level board control functions for the FPGA ADC capture system.
"""

import os
import yaml
import time

from pixels._log import get_logger
from .comms import Connection

_log = get_logger(__name__)
from .protocol import (
    TARGET_ADC_BOARD_CONFIG as T,
    TARGET_LOCKIN_CONFIG as TL,
    OP_TX_EN,
    OP_SLOW_CLK_MAX,
    OP_ODR_HIGH_TIME,
    OP_ODR_WAIT_FIRST,
    OP_ODR_LOW_TIME,
    OP_ODR_WAIT_LAST,
    OP_TX_GATE_EN,
    OP_CLK_CONFIG,
    OP_SET_MODE,
    send_commands,
)

_CLOCKS_YAML = os.path.join(os.path.dirname(__file__), "..", "configs", "clocks.yaml")


def adc_enable(enable: bool, conn: Connection, **kw) -> None:
    send_commands([(T, OP_TX_EN, 1 if enable else 0)], conn, **kw)


def set_odr_high_time(val: int, conn: Connection, **kw) -> None:
    send_commands([(T, OP_ODR_HIGH_TIME, val)], conn, **kw)


def set_odr_low_time(val: int, conn: Connection, **kw) -> None:
    send_commands([(T, OP_ODR_LOW_TIME, val)], conn, **kw)


def set_odr_wait_first(val: int, conn: Connection, **kw) -> None:
    send_commands([(T, OP_ODR_WAIT_FIRST, val)], conn, **kw)


def set_odr_wait_last(val: int, conn: Connection, **kw) -> None:
    send_commands([(T, OP_ODR_WAIT_LAST, val)], conn, **kw)


def set_slow_clk_max(val: int, conn: Connection, **kw) -> None:
    send_commands([(T, OP_SLOW_CLK_MAX, val)], conn, **kw)


def set_odr_timing(
    high: int, wait_first: int, low: int, wait_last: int, conn: Connection, **kw
) -> None:
    send_commands(
        [
            (T, OP_ODR_HIGH_TIME, high),
            (T, OP_ODR_WAIT_FIRST, wait_first),
            (T, OP_ODR_LOW_TIME, low),
            (T, OP_ODR_WAIT_LAST, wait_last),
        ],
        conn,
        **kw,
    )


def set_clk_config(sel: int, conn: Connection, **kw) -> None:
    if not 0 <= sel <= 4:
        raise ValueError(f"config_sel must be 0–4, got {sel}")
    send_commands([(T, OP_CLK_CONFIG, sel)], conn, **kw)


def set_odr_config(idx: int, conn: Connection, lut_path: str = None, **kw) -> None:
    """
    Switch to a pre-defined ODR configuration by index using configs/clocks.yaml.
    The pipeline must be disabled (tx_enable off) before calling.
    """

    path = lut_path or _CLOCKS_YAML
    with open(path) as f:
        lut = yaml.safe_load(f)

    if idx not in lut:
        raise ValueError(
            f"ODR config index {idx} not found in {path} (valid: {sorted(lut)})"
        )

    entry = lut[idx]

    send_commands([(T, OP_CLK_CONFIG, idx)], conn, **kw)
    time.sleep(0.020)

    send_commands(
        [
            (T, OP_ODR_HIGH_TIME, entry["odr_high_time"]),
            (T, OP_ODR_WAIT_FIRST, entry["odr_wait_first"]),
            (T, OP_ODR_LOW_TIME, entry["odr_low_time"]),
            (T, OP_ODR_WAIT_LAST, entry["odr_wait_last"]),
        ],
        conn,
        **kw,
    )

    _log.info(
        "ODR config %d: DCLK=%.3f MHz  high=%s wait1=%s low=%s waitL=%s",
        idx,
        entry["dclk_hz"] / 1e6,
        entry["odr_high_time"],
        entry["odr_wait_first"],
        entry["odr_low_time"],
        entry["odr_wait_last"],
    )


def set_demod_mode(demod: bool, conn: Connection, **kw) -> None:
    """Switch all four Ethernet ports between raw (demod=False) and demodulated I/Q (demod=True)."""
    send_commands([(TL, OP_SET_MODE, 1 if demod else 0)], conn, **kw)


def tx_enable(enable: bool, conn: Connection, **kw) -> None:
    """
    Enable or disable the full ADC+TX pipeline.

    On enable:  TX_EN=1 -> 20 ms hold-off -> TX_GATE_EN=1
    On disable: TX_GATE_EN=0 -> 3 ms drain -> TX_EN=0
    """
    if enable:
        send_commands([(T, OP_TX_EN, 1)], conn, **kw)
        time.sleep(0.020)
        send_commands([(T, OP_TX_GATE_EN, 1)], conn, **kw)
    else:
        send_commands([(T, OP_TX_GATE_EN, 0)], conn, **kw)
        time.sleep(0.003)
        send_commands([(T, OP_TX_EN, 0)], conn, **kw)
