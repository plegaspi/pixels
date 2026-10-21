import argparse
import os
import sys

from pixels.control import (
    Connection,
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
    send_commands,
    TARGET_ADC_BOARD_CONFIG,
)

_CONNECTIONS_YAML = os.path.join(
    os.path.dirname(__file__), "..", "configs", "connections.yaml"
)

_DEFAULT_FPGA_IP = "192.168.1.10"
_DEFAULT_FPGA_PORT = 1234
_DEFAULT_HOST_IP = "192.168.1.112"


def _load_connection_defaults(eth: str = "eth0") -> dict:
    try:
        import yaml

        with open(_CONNECTIONS_YAML) as f:
            cfg = yaml.safe_load(f) or {}
        entry = cfg.get(eth, {})
        return {
            "fpga_ip": entry.get("board_address") or _DEFAULT_FPGA_IP,
            "fpga_port": int(entry.get("port") or _DEFAULT_FPGA_PORT),
            "host_ip": entry.get("interface") or _DEFAULT_HOST_IP,
        }
    except (OSError, ImportError):
        return {
            "fpga_ip": _DEFAULT_FPGA_IP,
            "fpga_port": _DEFAULT_FPGA_PORT,
            "host_ip": _DEFAULT_HOST_IP,
        }


def main() -> None:
    defaults = _load_connection_defaults()

    parser = argparse.ArgumentParser(
        description="Send UDP commands to the FPGA ADC capture system."
    )
    parser.add_argument(
        "--fpga-ip",
        default=defaults["fpga_ip"],
        help=f"FPGA IP (default: {defaults['fpga_ip']})",
    )
    parser.add_argument(
        "--fpga-port",
        default=defaults["fpga_port"],
        type=int,
        help=f"FPGA UDP port (default: {defaults['fpga_port']})",
    )
    parser.add_argument(
        "--host-ip",
        default=defaults["host_ip"],
        help=f"Source IP (default: {defaults['host_ip']})",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress verbose output"
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tx-enable", help="Enable or disable the full ADC+TX pipeline")
    p.add_argument("state", choices=["on", "off"])

    p = sub.add_parser(
        "adc-enable", help="Enable or disable ADC clock only (no TX gate)"
    )
    p.add_argument("state", choices=["on", "off"])

    p = sub.add_parser(
        "odr-timing", help="Set all four ODR timing parameters in one packet"
    )
    p.add_argument("--high", type=int, required=True, help="ODR high time (cycles)")
    p.add_argument("--wait-first", type=int, required=True, help="Wait-first cycles")
    p.add_argument("--low", type=int, required=True, help="ODR low time (cycles)")
    p.add_argument("--wait-last", type=int, required=True, help="Wait-last cycles")

    for name, help_text in [
        ("odr-high", "ODR high time (cycles)"),
        ("odr-low", "ODR low time (cycles)"),
        ("odr-wait-first", "ODR wait-first cycles"),
        ("odr-wait-last", "ODR wait-last cycles"),
        ("slow-clk-max", "Slow clock max divider"),
    ]:
        p = sub.add_parser(name, help=f"Set {help_text}")
        p.add_argument("value", type=int)

    p = sub.add_parser(
        "odr-config",
        help="Switch to a pre-defined ODR config (0–4) from configs/clocks.yaml "
        "(run tx-enable off first, tx-enable on after)",
    )
    p.add_argument(
        "idx",
        type=int,
        choices=range(5),
        metavar="IDX",
        help="Config index: 0=41.6 MHz, 1–4 see configs/clocks.yaml",
    )
    p.add_argument("--lut", default=None, help="Path to clocks.yaml override")

    p = sub.add_parser("clk-config", help="Select MMCM frequency config (0–4)")
    p.add_argument("sel", type=int, choices=range(5), metavar="SEL")

    p = sub.add_parser(
        "raw", help="Send a raw command: target opcode arg0 (hex or decimal)"
    )
    p.add_argument("target", type=lambda x: int(x, 0))
    p.add_argument("opcode", type=lambda x: int(x, 0))
    p.add_argument("arg0", type=lambda x: int(x, 0))

    args = parser.parse_args()
    conn = Connection(
        fpga_ip=args.fpga_ip, fpga_port=args.fpga_port, host_ip=args.host_ip
    )
    verbose = not args.quiet

    if args.cmd == "tx-enable":
        tx_enable(args.state == "on", conn, verbose=verbose)
    elif args.cmd == "adc-enable":
        adc_enable(args.state == "on", conn, verbose=verbose)
    elif args.cmd == "odr-timing":
        set_odr_timing(
            args.high, args.wait_first, args.low, args.wait_last, conn, verbose=verbose
        )
    elif args.cmd == "odr-high":
        set_odr_high_time(args.value, conn, verbose=verbose)
    elif args.cmd == "odr-low":
        set_odr_low_time(args.value, conn, verbose=verbose)
    elif args.cmd == "odr-wait-first":
        set_odr_wait_first(args.value, conn, verbose=verbose)
    elif args.cmd == "odr-wait-last":
        set_odr_wait_last(args.value, conn, verbose=verbose)
    elif args.cmd == "slow-clk-max":
        set_slow_clk_max(args.value, conn, verbose=verbose)
    elif args.cmd == "odr-config":
        set_odr_config(args.idx, conn, lut_path=args.lut, verbose=verbose)
    elif args.cmd == "clk-config":
        set_clk_config(args.sel, conn, verbose=verbose)
    elif args.cmd == "raw":
        send_commands([(args.target, args.opcode, args.arg0)], conn, verbose=verbose)
