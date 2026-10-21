import socket

from .comms import Connection
from .protocol import (
    TARGET_SPI_CONFIG,
    OP_CS_SEL,
    OP_REG_ADDR,
    OP_REG_RD,
    OP_REG_WR,
    build_packet,
    send_commands,
)


def spi_cs_sel(index: int, conn: Connection, **kw) -> None:
    if not 0 <= index <= 31:
        raise ValueError(f"CS index must be 0–31, got {index}")
    send_commands([(TARGET_SPI_CONFIG, OP_CS_SEL, index)], conn, **kw)


def spi_reg_addr(addr: int, conn: Connection, **kw) -> None:
    if not 0 <= addr <= 0xFF:
        raise ValueError(f"Register address must be 0–255, got {addr}")
    send_commands([(TARGET_SPI_CONFIG, OP_REG_ADDR, addr)], conn, **kw)


def spi_reg_wr(addr: int, data: int, cs: int, conn: Connection, **kw) -> None:
    """Write one register. Sends CS_SEL + REG_ADDR + REG_WR in a single packet."""
    if not 0 <= cs <= 31:
        raise ValueError(f"CS index must be 0–31, got {cs}")
    if not 0 <= addr <= 0xFF:
        raise ValueError(f"Register address must be 0–255, got {addr}")
    send_commands(
        [
            (TARGET_SPI_CONFIG, OP_CS_SEL, cs),
            (TARGET_SPI_CONFIG, OP_REG_ADDR, addr),
            (TARGET_SPI_CONFIG, OP_REG_WR, data & 0xFF),
        ],
        conn,
        **kw,
    )


def open_response_socket(conn: Connection) -> socket.socket:
    """Open and return a UDP socket suitable for sending SPI read requests and
    receiving the FPGA's 4-byte response on the same port.

    The caller is responsible for closing the socket.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((conn.host_ip, conn.fpga_port))
    return s


def spi_reg_rd(
    addr: int,
    cs: int,
    conn: Connection,
    timeout: float = 2.0,
    sock: socket.socket | None = None,
    **kw,
) -> int | None:
    """Read one register. Sends CS_SEL + REG_ADDR + REG_RD on *sock* (or a
    temporary socket if none is provided) and waits for the 4-byte UDP response:
    0xAA cs_sel reg_addr reg_data.

    Pass a pre-opened socket from open_response_socket() when reading multiple
    registers in sequence to avoid repeated bind/close overhead.

    Returns reg_data as an int, or None on timeout.
    ADC stream must be stopped (tx_gate_en=0) before calling.
    """
    owned = sock is None
    if owned:
        sock = open_response_socket(conn)
    sock.settimeout(timeout)
    try:
        pkt = build_packet([
            (TARGET_SPI_CONFIG, OP_CS_SEL, cs),
            (TARGET_SPI_CONFIG, OP_REG_ADDR, addr),
            (TARGET_SPI_CONFIG, OP_REG_RD, 0),
        ])
        sock.sendto(pkt, (conn.fpga_ip, conn.fpga_port))
        while True:
            data, _ = sock.recvfrom(16)
            if len(data) == 4 and data[0] == 0xAA and data[1] == cs and data[2] == addr:
                return data[3]
    except TimeoutError:
        return None
    finally:
        if owned:
            sock.close()
