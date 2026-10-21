from pixels.configs import load_data_yaml, load_connections_yaml, get_interface_ips
from .profiles import N_STREAMS, profile_from_config

_d = load_data_yaml()
_adx134 = _d.get("adx134", {})
_cap = _adx134.get("capture", {})
_ports_data = _adx134.get("ports", {})
_profile = profile_from_config(_d)

_conn = load_connections_yaml()

# ── Capture parameters ────────────────────────────────────────────────────────
CAPTURE_MODE: str      = _profile.mode
DEMOD_MODE: bool       = _profile.demod_mode
N_CHAN_PER_PORT: int   = _profile.n_chan_per_port
SAMPLES_PER_FRAME: int = _profile.samples_per_frame
PAYLOAD_BYTES: int     = _profile.payload_bytes
N_WORDS: int           = PAYLOAD_BYTES // 4
SCRATCH_FORMAT: str    = _cap.get("scratch_format", "blosc_zstd")
WRITE_BATCH: int       = _cap.get("write_batch", 512)

UDP_PORT: int = 1234


def _build_port_map() -> list[dict]:
    """The board's single 10G connection (connections.adx134.eth0).

    All four data streams arrive in one datagram on this connection; any
    eth1..eth3 entries left over from the 4-port setup are ignored.
    """
    iface_ips = get_interface_ips()
    ports = []
    for key in ["eth0"] if "eth0" in _conn else []:
        net = _conn[key]
        host_ip = net.get("host_ip", "")
        if host_ip == "auto":
            iface_name = net.get("interface", "")
            host_ip = iface_ips.get(iface_name, "")
        ports.append({
            "remote_ip":  net.get("remote_ip", ""),
            "host_ip":    host_ip,
            "port":       int(net.get("port", UDP_PORT)),
        })
    return ports


PORT_MAP: list[dict] = _build_port_map()
# Data streams (= spool files, = port groups of channels), independent of the
# number of network connections.
N_PORTS: int = N_STREAMS
TOTAL_CHANNELS: int = N_PORTS * N_CHAN_PER_PORT
