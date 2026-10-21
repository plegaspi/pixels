"""
Template: HDF5 save and load for device buffers.
Copy to src/pixels/daq/<device>/packet_store.py and replace every TODO.
See docs/gui/updates.md for the full integration checklist.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from pixels.daq._base import SampleBatch

# TODO: import constants once values are filled in
# from .constants import LSB, V_REF, CHANNEL_LABELS

__all__ = ["save_hdf5", "load_hdf5"]


def save_hdf5(buf, path: str) -> None:
    """
    Write device buffers to HDF5.

    TODO: adapt `buf` type and structure to match what the device accumulates.
    See src/pixels/daq/ads1219/packet_store.py for a per-channel deque example,
    or src/pixels/daq/adx134/packet_store.py for a raw-packet spool writer.
    """
    # TODO: implement
    #
    # Minimal pattern (per-channel deques -> HDF5 groups):
    #
    # with h5py.File(path, "w") as f:
    #     f.attrs["v_ref"]      = V_REF
    #     f.attrs["lsb"]        = LSB
    #     f.attrs["n_channels"] = len(buf)
    #     for ch_idx, (times, raws) in buf.items():
    #         grp = f.create_group(f"ch{ch_idx}")
    #         grp.create_dataset("time",    data=np.array(times, dtype=np.float64))
    #         grp.create_dataset("raw",     data=np.array(raws,  dtype=np.int32))
    #         grp.create_dataset("voltage", data=np.array(raws,  dtype=np.float64) * LSB)
    #         grp.attrs["channel"]   = ch_idx
    #         grp.attrs["n_samples"] = len(raws)
    raise NotImplementedError


def load_hdf5(path: str) -> dict[int, SampleBatch]:
    """
    Load a saved HDF5 file.

    Returns a dict mapping channel index -> SampleBatch.
    TODO: adapt to match the structure written by save_hdf5.
    """
    # TODO: implement
    #
    # Minimal pattern:
    #
    # result: dict[int, SampleBatch] = {}
    # with h5py.File(path, "r") as f:
    #     lsb = float(f.attrs.get("lsb", LSB))
    #     for key in f:
    #         if not key.startswith("ch"):
    #             continue
    #         grp = f[key]
    #         ch  = int(grp.attrs["channel"])
    #         t   = np.array(grp["time"],    dtype=np.float64)
    #         raw = np.array(grp["raw"],     dtype=np.int32)
    #         volt = raw.astype(np.float64) * lsb
    #         result[ch] = SampleBatch(
    #             data=volt[:, np.newaxis],
    #             time=t - t[0] if len(t) else t,
    #             channel_labels=[CHANNEL_LABELS[ch]],
    #             units="V",
    #             metadata={"channel": ch, "lsb": lsb},
    #         )
    # return result
    raise NotImplementedError
