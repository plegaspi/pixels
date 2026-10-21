import os
from typing import Iterable

import h5py
import numpy as np

from pixels.compression import (
    BLOSC_ZSTD,
    compression_kwargs,
    require_hdf5plugin_for_dataset,
)

from .profiles import ADX134Profile, RAW_PROFILE, profile_from_attrs, get_adx134_profile

SCRATCH_FORMAT_BIN = "bin"
SCRATCH_FORMAT_HDF5_LZF = "hdf5_lzf"
SCRATCH_FORMATS = {SCRATCH_FORMAT_BIN, SCRATCH_FORMAT_HDF5_LZF, BLOSC_ZSTD}


def normalize_scratch_format(value: str | None) -> str:
    fmt = (value or SCRATCH_FORMAT_HDF5_LZF).strip().lower()
    aliases = {
        "binary": SCRATCH_FORMAT_BIN,
        "raw": SCRATCH_FORMAT_BIN,
        "h5": SCRATCH_FORMAT_HDF5_LZF,
        "hdf5": SCRATCH_FORMAT_HDF5_LZF,
        "lzf": SCRATCH_FORMAT_HDF5_LZF,
        "zstd": BLOSC_ZSTD,
        "blosc": BLOSC_ZSTD,
    }
    fmt = aliases.get(fmt, fmt)
    if fmt not in SCRATCH_FORMATS:
        raise ValueError(
            f"Unsupported scratch_format '{value}'. Use one of: {', '.join(sorted(SCRATCH_FORMATS))}"
        )
    return fmt


def capture_path(bin_dir: str, port_idx: int, scratch_format: str) -> str:
    fmt = normalize_scratch_format(scratch_format)
    ext = ".bin" if fmt == SCRATCH_FORMAT_BIN else ".h5"
    return os.path.join(bin_dir, f"capture_port{port_idx}{ext}")


def find_capture_paths(bin_dir: str, n_ports: int) -> list[str]:
    paths = []
    for i in range(n_ports):
        h5_path = capture_path(bin_dir, i, SCRATCH_FORMAT_HDF5_LZF)
        bin_path = capture_path(bin_dir, i, SCRATCH_FORMAT_BIN)
        if os.path.exists(h5_path):
            paths.append(h5_path)
        elif os.path.exists(bin_path):
            paths.append(bin_path)
        else:
            raise FileNotFoundError(
                f"Missing capture file for port {i}: expected '{h5_path}' or '{bin_path}'"
            )
    return paths


class PacketSpoolWriter:
    def __init__(
        self,
        path: str,
        scratch_format: str,
        chunk_packets: int,
        attrs: dict | None = None,
        profile: ADX134Profile | None = None,
    ):
        self.path = path
        self.scratch_format = normalize_scratch_format(scratch_format)
        self.chunk_packets = max(1, int(chunk_packets))
        self.profile = profile or get_adx134_profile((attrs or {}).get("capture_mode"))
        self.payload_bytes = self.profile.payload_bytes
        self.n_words = self.profile.n_words
        self.attrs = attrs or {}
        self._buffer: list[bytes] = []
        self._fout = None
        self._h5: h5py.File | None = None
        self._ds: h5py.Dataset | None = None
        self.n_packets = 0

    def __enter__(self):
        if self.scratch_format == SCRATCH_FORMAT_BIN:
            self._fout = open(self.path, "wb", buffering=8 * 1024 * 1024)
        else:
            h5_compression = (
                compression_kwargs(BLOSC_ZSTD, blosc_clevel=1)
                if self.scratch_format == BLOSC_ZSTD
                else {"compression": "lzf", "shuffle": True}
            )
            self._h5 = h5py.File(self.path, "w", libver="latest")
            self._ds = self._h5.create_dataset(
                "packets",
                shape=(0, self.n_words),
                maxshape=(None, self.n_words),
                chunks=(self.chunk_packets, self.n_words),
                dtype=">u4",
                **h5_compression,
            )
            self._h5.attrs["scratch_format"] = self.scratch_format
            for key, value in self.profile.attrs(self.attrs.get("odr_hz")).items():
                self._h5.attrs[key] = value
            for key, value in self.attrs.items():
                self._h5.attrs[key] = value
            self._h5.swmr_mode = True
        return self

    def write(self, data: bytes) -> None:
        if len(data) != self.payload_bytes:
            raise ValueError(f"Expected {self.payload_bytes} bytes, got {len(data)}")
        if self.scratch_format == SCRATCH_FORMAT_BIN:
            assert self._fout is not None
            self._fout.write(data)
            self.n_packets += 1
            return

        self._buffer.append(bytes(data))
        if len(self._buffer) >= self.chunk_packets:
            self.flush()

    def flush(self) -> None:
        if self.scratch_format == SCRATCH_FORMAT_BIN:
            if self._fout is not None:
                self._fout.flush()
            return
        if not self._buffer:
            return
        assert self._ds is not None
        assert self._h5 is not None
        words = np.frombuffer(b"".join(self._buffer), dtype=">u4").reshape(
            len(self._buffer), self.n_words
        )
        old_rows = self._ds.shape[0]
        new_rows = old_rows + words.shape[0]
        self._ds.resize((new_rows, self.n_words))
        self._ds[old_rows:new_rows] = words
        self.n_packets = new_rows
        self._buffer.clear()
        self._ds.flush()
        self._h5.flush()

    def __exit__(self, exc_type, exc, tb):
        self.flush()
        if self._fout is not None:
            self._fout.close()
        if self._h5 is not None:
            self._h5.close()


class PacketReader:
    def __init__(self, path: str, swmr: bool = False, profile: ADX134Profile | None = None):
        self.path = path
        self.is_hdf5 = path.endswith((".h5", ".hdf5"))
        self.profile = profile or RAW_PROFILE
        self.payload_bytes = self.profile.payload_bytes
        self.n_words = self.profile.n_words
        self.trailing_bytes = 0
        self._h5: h5py.File | None = None
        self._ds: h5py.Dataset | None = None
        self._mm: np.memmap | None = None
        if self.is_hdf5:
            self._h5 = h5py.File(path, "r", swmr=swmr)
            self._ds = self._h5["packets"]
            require_hdf5plugin_for_dataset(self._ds)
            self.profile = profile_from_attrs(self._h5.attrs, self.profile)
            self.payload_bytes = self.profile.payload_bytes
            self.n_words = int(self._h5.attrs.get("n_words", self._ds.shape[1]))
            self.n_packets = int(self._ds.shape[0])
        else:
            size = os.path.getsize(path)
            if profile is None:
                for candidate in (RAW_PROFILE, get_adx134_profile("demod")):
                    if size > 0 and size % candidate.payload_bytes == 0:
                        self.profile = candidate
                        break
            self.payload_bytes = self.profile.payload_bytes
            self.n_words = self.profile.n_words
            self.n_packets = size // self.payload_bytes
            self.trailing_bytes = size % self.payload_bytes
            self._mm = np.memmap(
                path, dtype=np.uint8, mode="r", shape=(self.n_packets, self.payload_bytes)
            )

    def refresh(self) -> None:
        if self._ds is not None:
            self._ds.refresh()
            self.n_packets = int(self._ds.shape[0])

    def frame_seq(self, index: int) -> int:
        if self._ds is not None:
            return int((int(self._ds[index, 0]) >> 24) & 0xFF)
        assert self._mm is not None
        return int(self._mm[index, 0])

    def frame_seqs(self, start: int = 0, stop: int | None = None) -> np.ndarray:
        stop = self.n_packets if stop is None else stop
        if self._ds is not None:
            words = np.asarray(self._ds[start:stop, 0], dtype=np.uint32)
            return ((words >> 24) & 0xFF).astype(np.int32)
        assert self._mm is not None
        return self._mm[start:stop, 0].astype(np.int32)

    def find_frame_seq(self, target_seq: int) -> np.ndarray:
        return np.where(self.frame_seqs() == target_seq)[0]

    def read_words(self, start: int, stop: int) -> np.ndarray:
        if self._ds is not None:
            return np.asarray(self._ds[start:stop])
        assert self._mm is not None
        pkts = np.ascontiguousarray(self._mm[start:stop])
        return pkts.view(">u4").reshape(stop - start, self.n_words)

    def close(self) -> None:
        self._mm = None
        if self._h5 is not None:
            self._h5.close()
            self._h5 = None
            self._ds = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def open_packet_readers(
    paths: Iterable[str],
    swmr: bool = False,
    profile: ADX134Profile | None = None,
) -> list[PacketReader]:
    return [PacketReader(path, swmr=swmr, profile=profile) for path in paths]
