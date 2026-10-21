"""HDF5 save/load for ADS1219 per-channel ring buffers."""

from __future__ import annotations

from collections import deque

import h5py
import numpy as np

from pixels.daq._base import SampleBatch
from .constants import LSB, MUX_LABELS, V_REF

__all__ = ["save_hdf5", "load_hdf5"]


def save_hdf5(buf: dict[int, tuple[deque, deque]], path: str) -> None:
    """Write per-channel ring buffers to HDF5. Each MUX channel gets its own group."""
    with h5py.File(path, "w") as f:
        f.attrs["v_ref"] = V_REF
        f.attrs["lsb"] = LSB
        f.attrs["n_channels"] = len(buf)
        for mux, (t_d, r_d) in buf.items():
            t = np.array(t_d, dtype=np.float64)
            raw = np.array(r_d, dtype=np.int32)
            grp = f.create_group(f"ch{mux}")
            grp.create_dataset("time", data=t)
            grp.create_dataset("raw", data=raw)
            grp.create_dataset("voltage", data=raw.astype(np.float64) * LSB)
            grp.attrs["mux"] = mux
            grp.attrs["n_samples"] = len(raw)


def load_hdf5(path: str) -> dict[int, SampleBatch]:
    """
    Load a saved ADS1219 HDF5 file.

    Returns a dict mapping MUX value -> SampleBatch, one entry per recorded channel.
    """
    result: dict[int, SampleBatch] = {}
    with h5py.File(path, "r") as f:
        lsb = float(f.attrs.get("lsb", LSB))
        for key in f:
            if not key.startswith("ch"):
                continue
            grp = f[key]
            mux = int(grp.attrs["mux"])
            t = np.array(grp["time"], dtype=np.float64)
            raw = np.array(grp["raw"], dtype=np.int32)
            volt = raw.astype(np.float64) * lsb
            result[mux] = SampleBatch(
                data=volt[:, np.newaxis],
                time=t - t[0] if len(t) else t,
                channel_labels=[MUX_LABELS.get(mux, f"MUX{mux}")],
                units="V",
                metadata={"mux": mux, "lsb": lsb},
            )
    return result
