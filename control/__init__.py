from .comms import Connection, send_packet
from .protocol import (
    START_WORD,
    PROTO_VER,
    TARGET_ADC_BOARD_CONFIG,
    OP_TX_EN,
    OP_SLOW_CLK_MAX,
    OP_ODR_HIGH_TIME,
    OP_ODR_WAIT_FIRST,
    OP_ODR_LOW_TIME,
    OP_ODR_WAIT_LAST,
    OP_TX_GATE_EN,
    OP_CLK_CONFIG,
    build_packet,
    send_commands,
)
from .board import (
    adc_enable,
    tx_enable,
    set_odr_timing,
    set_odr_config,
    set_clk_config,
    set_odr_high_time,
    set_odr_low_time,
    set_odr_wait_first,
    set_odr_wait_last,
    set_slow_clk_max,
)
from .spi import (
    spi_cs_sel,
    spi_reg_addr,
    spi_reg_wr,
    spi_reg_rd,
)
