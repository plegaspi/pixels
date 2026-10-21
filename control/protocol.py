"""
Protocol constants and packet construction for the FPGA ADC capture system.

Packet format (big-endian 32-bit words):
  Word 0:  0xDEADBEEF
  Word 1:  0x01_00_00_NN   version=1, reserved, reserved, num_cmds
  Per command (3 words = 12 bytes):
    Word 0:  TT_OO_00_00   target(8) opcode(8) flags(16, must be 0x0000)
    Word 1:  AAAAAAAA      arg0 (32-bit)
    Word 2:  00000000      arg1 (reserved)
"""

import struct
from .comms import Connection, send_packet

# ── Constants ─────────────────────────────────────────────────────────────────

START_WORD = 0xDEADBEEF
PROTO_VER = 0x01

# Targets
TARGET_SPI_CONFIG = 0x01
TARGET_ADC_BOARD_CONFIG = 0x02
TARGET_LOCKIN_CONFIG = 0x03

# Opcodes — TARGET_SPI_CONFIG
OP_CS_SEL = 0x01  # arg0[4:0]:  CS output index (0-31)
OP_REG_ADDR = 0x02  # arg0[7:0]:  SPI register address
OP_REG_WR = 0x03  # arg0[31:0]: SPI register write data; also toggles irq_tog
OP_REG_RD = 0x04  # no arg:     read reg_addr; PS returns result via UDP response packet

# Opcodes — TARGET_ADC_BOARD_CONFIG
OP_TX_EN = 0x01
OP_SLOW_CLK_MAX = 0x02
OP_ODR_HIGH_TIME = 0x03
OP_ODR_WAIT_FIRST = 0x04
OP_ODR_LOW_TIME = 0x05
OP_ODR_WAIT_LAST = 0x06
OP_TX_GATE_EN = 0x07
OP_CLK_CONFIG = 0x08

# Opcodes — TARGET_LOCKIN_CONFIG
OP_SET_MODE = 0x01  # arg0[0]: 0 = raw pass-through, 1 = demodulated I/Q

_TARGET_NAMES = {
    TARGET_SPI_CONFIG: "SPI_CONFIG",
    TARGET_ADC_BOARD_CONFIG: "ADC_BOARD_CONFIG",
    TARGET_LOCKIN_CONFIG: "LOCKIN_CONFIG",
}
_OPCODE_NAMES = {
    OP_CS_SEL: "CS_SEL",
    OP_REG_ADDR: "REG_ADDR",
    OP_REG_WR: "REG_WR",
    OP_REG_RD: "REG_RD",
    OP_TX_EN: "TX_EN",
    OP_SLOW_CLK_MAX: "SLOW_CLK_MAX",
    OP_ODR_HIGH_TIME: "ODR_HIGH_TIME",
    OP_ODR_WAIT_FIRST: "ODR_WAIT_FIRST",
    OP_ODR_LOW_TIME: "ODR_LOW_TIME",
    OP_ODR_WAIT_LAST: "ODR_WAIT_LAST",
    OP_TX_GATE_EN: "TX_GATE_EN",
    OP_CLK_CONFIG: "CLK_CONFIG",
    OP_SET_MODE: "SET_MODE",
}

# ── Packet builder ────────────────────────────────────────────────────────────


def build_packet(commands: list[tuple[int, int, int]]) -> bytes:
    n = len(commands)
    if not 1 <= n <= 255:
        raise ValueError(f"Command count must be 1–255, got {n}")
    header = struct.pack(">II", START_WORD, (PROTO_VER << 24) | n)
    body = b"".join(
        struct.pack(">III", (target << 24) | (opcode << 16), arg0, 0)
        for target, opcode, arg0 in commands
    )
    pkt = header + body
    assert len(pkt) == 8 + n * 12
    return pkt


# ── Transport ─────────────────────────────────────────────────────────────────


def send_commands(
    commands: list[tuple[int, int, int]],
    conn: Connection,
    verbose: bool = True,
) -> None:
    pkt = build_packet(commands)
    send_packet(pkt, conn)
    if verbose:
        from pixels._log import get_logger
        _log = get_logger(__name__)
        _log.debug("Sent %d bytes to %s:%d (%d cmd(s))",
                   len(pkt), conn.fpga_ip, conn.fpga_port, len(commands))
        for target, opcode, arg0 in commands:
            tname = _TARGET_NAMES.get(target, f"0x{target:02X}")
            oname = _OPCODE_NAMES.get(opcode, f"0x{opcode:02X}")
            _log.debug("  %s.%s  arg0=0x%08X (%d)", tname, oname, arg0, arg0)
        for i in range(0, len(pkt), 4):
            word = pkt[i : i + 4]
            _log.debug("  [%3d] %s  (%10d)", i // 4, word.hex().upper(),
                       int.from_bytes(word, 'big'))
