from pixels.configs import load_data_yaml

_cap = load_data_yaml().get("adx134", {}).get("capture", {})

V_REF: float  = _cap.get("v_ref",  4.096)
RESOLUTION: int = _cap.get("resolution", 24)
LSB: float    = V_REF * 2 / (2 ** RESOLUTION)   # full-scale / 2^resolution

FILENAME: str  = _cap.get("filename",   "capture.hdf5")
WRITE_BATCH: int = _cap.get("write_batch", 512)
FINAL_COMPRESSION: str = _cap.get("final_compression", "blosc_zstd")
FINAL_COMPRESSION_OPTS: int | None = _cap.get("final_compression_opts", 1)
