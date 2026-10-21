import socket
from dataclasses import dataclass, field


@dataclass
class Connection:
    fpga_ip: str
    fpga_port: int
    host_ip: str


def send_packet(pkt: bytes, conn: Connection) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((conn.host_ip, 0))
        s.sendto(pkt, (conn.fpga_ip, conn.fpga_port))
