import os

import h5py
import numpy as np

from pixels._log import get_logger

_log = get_logger(__name__)

from pixels.compression import compression_kwargs
from ..packet_store import find_capture_paths, open_packet_readers
from ..constants import (
    PORT_MAP,
    N_PORTS,
)
from ..profiles import ADX134Profile, get_adx134_profile, profile_from_config
from .constants import FILENAME, FINAL_COMPRESSION, FINAL_COMPRESSION_OPTS, WRITE_BATCH

PACKED_HEADER_WORDS = 1
PACKED_DATA_BYTES_PER_PACKET = 11 * 28 * 3


def _compression_kwargs(compression: str | None, compression_opts: int | None = None):
    return compression_kwargs(compression, compression_opts, blosc_clevel=3)


def convert_bin_to_hdf5(
    bin_dir: str = ".",
    n_ports: int = N_PORTS,
    filename: str = FILENAME,
    port_map=PORT_MAP,
    final_compression: str = FINAL_COMPRESSION,
    final_compression_opts: int | None = FINAL_COMPRESSION_OPTS,
    demod_mode: bool = False,
    profile: ADX134Profile | str | None = None,
    odr_hz: float | None = None,
):
    if isinstance(profile, str):
        profile = get_adx134_profile(profile)
    elif profile is None:
        try:
            from pixels.configs import load_data_yaml

            profile = profile_from_config(load_data_yaml())
        except Exception:
            profile = get_adx134_profile(demod_mode=demod_mode)
    demod_mode = profile.demod_mode
    n_chan_per_port = profile.n_chan_per_port
    samples_per_frame = profile.samples_per_frame
    payload_bytes = profile.payload_bytes
    packed_data_bytes = samples_per_frame * n_chan_per_port * 3

    if n_ports:
        bin_paths = find_capture_paths(bin_dir, n_ports)
    else:
        bin_paths = []

    # If n_ports=0 (e.g. no connections.yaml configured), fall back to auto-detection.
    if not bin_paths:
        import glob

        detected = sorted(
            glob.glob(os.path.join(bin_dir, "capture_port*.h5"))
            + glob.glob(os.path.join(bin_dir, "capture_port*.bin"))
        )
        if not detected:
            raise ValueError(
                f"No capture_port*.h5 or capture_port*.bin files found in '{bin_dir}'. "
                "Check that the correct bins directory is selected."
            )
        bin_paths = detected
        n_ports = len(detected)
        _log.info(f"Auto-detected {n_ports} port file(s) in '{bin_dir}'")

    _log.info("Opening capture files...")
    readers = open_packet_readers(bin_paths, profile=profile)
    for i, path in enumerate(bin_paths):
        size = os.path.getsize(path)
        reader = readers[i]
        if getattr(reader, "trailing_bytes", 0):
            _log.warning(
                "  port%d: trailing %d bytes ignored", i, reader.trailing_bytes
            )
        if i == 0:
            profile = reader.profile
            if odr_hz is None and getattr(reader, "_h5", None) is not None:
                odr_hz = float(reader._h5.attrs.get("odr_hz", 0.0)) or None
            demod_mode = profile.demod_mode
            n_chan_per_port = profile.n_chan_per_port
            samples_per_frame = profile.samples_per_frame
            payload_bytes = profile.payload_bytes
            packed_data_bytes = samples_per_frame * n_chan_per_port * 3
        _log.info(
            "  Port %d: %s packets = %s samples (%.1f MB)",
            i,
            f"{reader.n_packets:,}",
            f"{reader.n_packets * samples_per_frame:,}",
            size / 1e6,
        )

    # frame_seq lives in byte 0 of the packed packet header.
    first_seqs = [reader.frame_seq(0) for reader in readers]
    target_seq = max(first_seqs)
    _log.info("Syncing to frame_seq=%d...", target_seq)

    start_idxs = []
    for i, (reader, fs) in enumerate(zip(readers, first_seqs)):
        if fs == target_seq:
            si = 0
        else:
            matches = reader.find_frame_seq(target_seq)
            if len(matches) == 0:
                raise ValueError(f"Port {i}: frame_seq={target_seq} not found")
            si = int(matches[0])
            _log.info("  Port %d: skipping %d packets to sync", i, si)

        # Advance past any initial discontinuity (FPGA warm-up packets)
        probe = min(256, reader.n_packets - si)
        seqs = reader.frame_seqs(si, si + probe)
        diffs = np.diff(seqs)
        bad = np.where((diffs % 256) != 1)[0]
        if len(bad) > 0 and bad[0] < 16:
            trim = int(bad[0]) + 1
            si += trim
            _log.info("  Port %d: trimmed %d warm-up packet(s) at start", i, trim)

        start_idxs.append(si)

    n_pkts_common = min(
        reader.n_packets - si for reader, si in zip(readers, start_idxs)
    )
    if n_pkts_common <= 0:
        raise ValueError("No complete packets available after port sync")
    total_channels = n_ports * n_chan_per_port
    n_samples = n_pkts_common * samples_per_frame
    chunk_rows = min(n_samples, samples_per_frame * WRITE_BATCH)
    compression_kwargs = _compression_kwargs(final_compression, final_compression_opts)

    _log.info(
        "  %s packets -> %s samples × %d channels",
        f"{n_pkts_common:,}",
        f"{n_samples:,}",
        total_channels,
    )

    _log.info("Writing '%s'...", filename)
    with h5py.File(filename, "w") as f:
        raw_ds = f.create_dataset(
            "raw",
            shape=(n_samples, total_channels),
            chunks=(chunk_rows, total_channels),
            dtype="int32",
            **compression_kwargs,
        )

        n_chunks = (n_pkts_common + WRITE_BATCH - 1) // WRITE_BATCH
        for chunk_idx in range(n_chunks):
            p0 = chunk_idx * WRITE_BATCH
            p1 = min(p0 + WRITE_BATCH, n_pkts_common)
            chunk_pkts = p1 - p0
            s0 = p0 * samples_per_frame
            s1 = p1 * samples_per_frame

            row = np.empty(
                (chunk_pkts * samples_per_frame, total_channels), dtype=np.int32
            )

            for port_idx, (reader, si) in enumerate(zip(readers, start_idxs)):
                words = reader.read_words(si + p0, si + p1)
                payload_words = words[:, PACKED_HEADER_WORDS:].astype(">u4", copy=False)
                payload_bytes = payload_words.view(np.uint8).reshape(
                    chunk_pkts, packed_data_bytes
                )
                byte_codes = payload_bytes.reshape(
                    chunk_pkts, samples_per_frame, n_chan_per_port, 3
                ).astype(np.int32)
                codes = (
                    (byte_codes[:, :, :, 0] << 16)
                    | (byte_codes[:, :, :, 1] << 8)
                    | byte_codes[:, :, :, 2]
                )
                codes[codes >= 0x800000] -= 0x1000000
                offset = port_idx * n_chan_per_port
                row[:, offset : offset + n_chan_per_port] = codes.reshape(
                    chunk_pkts * samples_per_frame, n_chan_per_port
                )

            raw_ds[s0:s1] = row

            if (chunk_idx + 1) % 50 == 0 or chunk_idx == n_chunks - 1:
                pct = 100 * (chunk_idx + 1) / n_chunks
                _log.debug(
                    "  %5.1f%%  (%s / %s samples)", pct, f"{s1:,}", f"{n_samples:,}"
                )

        f.attrs["n_ports"] = n_ports
        f.attrs["total_channels"] = total_channels
        for key, value in profile.attrs(odr_hz).items():
            f.attrs[key] = value
        f.attrs["chan_per_port"] = n_chan_per_port
        f.attrs["final_compression"] = final_compression
        for i, cfg in enumerate(port_map):
            f.attrs[f"port{i}_remote_ip"] = cfg["remote_ip"]
            f.attrs[f"port{i}_host_ip"] = cfg["host_ip"]

    _log.info("Done: '%s'", filename)

    _log.info("Checking sequence numbers for drops...")
    total_drops = 0
    for i, (reader, si) in enumerate(zip(readers, start_idxs)):
        pkt_seqs = reader.frame_seqs(si, si + n_pkts_common)
        diffs = np.diff(pkt_seqs)
        bad = np.where((diffs % 256) != 1)[0]
        if len(bad):
            dropped = int(sum(((int(diffs[j]) - 1) % 256) for j in bad))
            total_drops += dropped
            _log.warning(
                "  Port %d: %d discontinuities, ~%d dropped packets",
                i,
                len(bad),
                dropped,
            )
            for j in bad[:20]:
                pkt_idx = int(j)
                sample_idx = pkt_idx * samples_per_frame
                n_here = int(((int(diffs[j]) - 1) % 256))
                _log.warning(
                    "    pkt %s  (sample %s)  seq %d->%d  ~%d dropped",
                    f"{pkt_idx:,}",
                    f"{sample_idx:,}",
                    pkt_seqs[j],
                    pkt_seqs[j + 1],
                    n_here,
                )
            if len(bad) > 20:
                _log.warning(
                    "    ... (%d more discontinuities not shown)", len(bad) - 20
                )
        else:
            _log.info("  Port %d: no drops detected", i)

    if total_drops:
        _log.warning("  Total: ~%d dropped packets across all ports", total_drops)
    else:
        _log.info("  Clean capture — no drops on any port")

    for reader in readers:
        reader.close()
