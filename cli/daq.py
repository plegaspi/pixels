import argparse
import os

from pixels.daq.adx134 import ADX134DAQ
from pixels.daq.adx134.constants import N_PORTS, PORT_MAP, SCRATCH_FORMAT, UDP_PORT
from pixels.daq.adx134.data import convert_bin_to_hdf5, postprocess
from pixels.daq.adx134.data.constants import FINAL_COMPRESSION, FILENAME
from pixels.daq.adx134.profiles import get_adx134_profile, profile_from_config


def main():
    parser = argparse.ArgumentParser(description="Capture ADC data over UDP and convert to HDF5.")
    parser.add_argument("--bin-dir", default=".", help="Directory for binary capture files (default: .)")
    parser.add_argument("--output", default=FILENAME, help=f"HDF5 output filename (default: {FILENAME})")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help=f"UDP port (default: {UDP_PORT})")
    parser.add_argument("--debug", action="store_true", help="Print socket debug info")
    parser.add_argument(
        "--scratch-format",
        default=SCRATCH_FORMAT,
        choices=("blosc_zstd", "hdf5_lzf", "bin"),
        help=f"Capture scratch format (default: {SCRATCH_FORMAT})",
    )
    parser.add_argument(
        "--final-compression",
        default=FINAL_COMPRESSION,
        choices=("blosc_zstd", "gzip", "lzf", "none"),
        help=f"Final HDF5 compression (default: {FINAL_COMPRESSION})",
    )
    parser.add_argument("--no-convert", action="store_true", help="Skip HDF5 conversion after capture")
    parser.add_argument("--no-postprocess", action="store_true", help="Skip voltage/hex postprocessing")
    parser.add_argument("--mode", choices=("raw", "demod"), default=None, help="ADX134 capture mode")

    args = parser.parse_args()

    if args.mode is None:
        from pixels.configs import load_data_yaml
        profile = profile_from_config(load_data_yaml())
    else:
        profile = get_adx134_profile(args.mode)

    daq = ADX134DAQ(
        port_map=PORT_MAP,
        udp_port=args.udp_port,
        bin_dir=args.bin_dir,
        debug=args.debug,
        scratch_format=args.scratch_format,
        profile=profile,
    )
    daq.run()

    if not args.no_convert:
        convert_bin_to_hdf5(
            bin_dir=args.bin_dir,
            n_ports=N_PORTS,
            filename=args.output,
            port_map=PORT_MAP,
            final_compression=args.final_compression,
            profile=profile,
        )

    if not args.no_convert and not args.no_postprocess:
        postprocess(args.output, final_compression=args.final_compression)
