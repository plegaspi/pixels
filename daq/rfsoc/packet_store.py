"""HDF5 save/load for RFSoC ADC captures."""

from __future__ import annotations

import numpy as np

from pixels.daq._base import SampleBatch
from .constants import CHANNEL_LABELS

__all__ = ["save_hdf5", "load_hdf5"]


def save_hdf5(
    data: np.ndarray,
    path: str,
    sample_rate: float,
    channel_labels: list[str] | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Write a (n_samples, n_channels) int16 array to HDF5.

    Parameters
    ----------
    data          : (n_samples, n_channels) int16
    path          : destination file path
    sample_rate   : Hz, stored as an attribute
    channel_labels: label for each channel column
    metadata      : extra key/value attributes to store
    """
    import h5py

    if data.ndim == 1:
        data = data[:, np.newaxis]

    n_channels = data.shape[1]
    labels = channel_labels or list(CHANNEL_LABELS[:n_channels])

    with h5py.File(path, "w") as f:
        ds = f.create_dataset("samples", data=data.astype(np.int16))
        ds.attrs["sample_rate"]    = float(sample_rate)
        ds.attrs["n_channels"]     = n_channels
        ds.attrs["n_samples"]      = len(data)
        ds.attrs["channel_labels"] = labels
        if metadata:
            for k, v in metadata.items():
                f.attrs[k] = v


def load_hdf5(path: str) -> dict[str, object]:
    """
    Load a saved RFSoC HDF5 file.

    Returns a dict with keys:
      ``samples``        (n_samples, n_channels) int16
      ``sample_rate``    float Hz
      ``channel_labels`` list[str]
    """
    import h5py

    with h5py.File(path, "r") as f:
        ds      = f["samples"]
        data    = np.array(ds, dtype=np.int16)
        sr      = float(ds.attrs.get("sample_rate", 0.0))
        labels  = list(ds.attrs.get("channel_labels", CHANNEL_LABELS[:data.shape[1]]))

    return {"samples": data, "sample_rate": sr, "channel_labels": labels}
