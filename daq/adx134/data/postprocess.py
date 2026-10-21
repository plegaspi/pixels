import h5py
import numpy as np

from pixels._log import get_logger
_log = get_logger(__name__)

from .constants import (
    FILENAME,
    FINAL_COMPRESSION,
    FINAL_COMPRESSION_OPTS,
    LSB,
    WRITE_BATCH,
)
from .conversion import _compression_kwargs

_PLL_F_OUT = 5_000.0        # Hz — FPGA demod output rate (500 kHz / DECIM_RATIO=100)
_PLL_CUTOFF_DEFAULT = 1_000.0


def _track_and_recover(
    I: np.ndarray,
    Q: np.ndarray,
    pll_cutoff: float = _PLL_CUTOFF_DEFAULT,
    phi_init: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """IIR carrier-phase tracker. I, Q: (n_samples, n_channels). Returns (recovered, phi_final)."""
    alpha = 1 - np.exp(-2 * np.pi * pll_cutoff / _PLL_F_OUT)
    n, c = I.shape
    raw_phi = np.arctan2(I, Q)
    phi_tracked = np.zeros((n, c))
    phi_prev = phi_init if phi_init is not None else raw_phi[0].copy()
    for k in range(n):
        delta = raw_phi[k] - phi_prev
        delta = (delta + np.pi) % (2 * np.pi) - np.pi
        phi_prev = alpha * (phi_prev + delta) + (1 - alpha) * phi_prev
        phi_tracked[k] = phi_prev
    return I * np.sin(phi_tracked) + Q * np.cos(phi_tracked), phi_prev


def postprocess(
    filename: str = FILENAME,
    final_compression: str = FINAL_COMPRESSION,
    final_compression_opts: int | None = FINAL_COMPRESSION_OPTS,
    pll_cutoff: float = _PLL_CUTOFF_DEFAULT,
):
    compression_kwargs = _compression_kwargs(final_compression, final_compression_opts)
    with h5py.File(filename, "a") as f:
        demod_mode = bool(f.attrs.get("demod_mode", False))
        if demod_mode:
            _postprocess_lockin(f, compression_kwargs, pll_cutoff)
        else:
            _postprocess_raw(f, compression_kwargs)
    _log.info("Done.")


def _postprocess_raw(f: h5py.File, compression_kwargs: dict) -> None:
    _log.info("Post-processing (raw mode): adding voltage and hex datasets...")
    raw_ds: h5py.Dataset = f["raw"]  # type: ignore[assignment]
    n_rows, total_ch = raw_ds.shape
    if n_rows <= 0:
        raise ValueError("raw dataset is empty")
    samples_per_frame = int(f.attrs.get("samples_per_frame", 11))
    chunk_rows = min(n_rows, samples_per_frame * WRITE_BATCH)

    for name in ("voltage", "hex"):
        if name in f:
            del f[name]

    volt_ds = f.create_dataset(
        "voltage",
        shape=(n_rows, total_ch),
        chunks=(chunk_rows, total_ch),
        dtype=np.float64,
        **compression_kwargs,
    )
    hex_ds = f.create_dataset(
        "hex",
        shape=(n_rows, total_ch),
        chunks=(chunk_rows, total_ch),
        dtype=np.uint32,
        **compression_kwargs,
    )

    n_chunks = (n_rows + chunk_rows - 1) // chunk_rows
    for i in range(n_chunks):
        r0 = i * chunk_rows
        r1 = min(r0 + chunk_rows, n_rows)
        raw_chunk: np.ndarray = raw_ds[r0:r1]  # type: ignore[assignment]
        volt_ds[r0:r1] = raw_chunk.astype(np.float64) * LSB
        hex_ds[r0:r1] = raw_chunk.astype(np.uint32)
        if (i + 1) % 50 == 0 or i == n_chunks - 1:
            pct = 100 * (i + 1) / n_chunks
            _log.debug("  %5.1f%%  (%s / %s rows)", pct, f"{r1:,}", f"{n_rows:,}")


def _postprocess_lockin(f: h5py.File, compression_kwargs: dict, pll_cutoff: float = _PLL_CUTOFF_DEFAULT) -> None:
    """
    Demodulated data layout (per port, N_CHAN_PER_PORT slots):
      slots 0 .. N/2-1  : I_avg  (in-phase)
      slots N/2 .. N-1  : Q_avg  (quadrature)

    Outputs:
      amplitude  (n_samples, n_ports * N/2)  sqrt(I^2 + Q^2) * LSB  [volts, ~22% low due to /128 approx]
      phase      (n_samples, n_ports * N/2)  atan2(Q, I)             [radians]
    """
    _log.info("Post-processing (demodulation mode): computing amplitude and phase...")
    raw_ds: h5py.Dataset = f["raw"]  # type: ignore[assignment]
    n_rows, total_ch = raw_ds.shape
    if n_rows <= 0:
        raise ValueError("raw dataset is empty")

    n_ports = int(f.attrs.get("n_ports", 1))
    cpp = total_ch // n_ports  # channels per port (28)
    n_real = cpp // 2  # real channels per port (14: I slots 0-13, Q slots 14-27)
    n_out_ch = n_ports * n_real

    samples_per_frame = int(f.attrs.get("samples_per_frame", 8))
    chunk_rows = min(n_rows, samples_per_frame * WRITE_BATCH)

    for name in ("amplitude", "phase", "recovered"):
        if name in f:
            del f[name]

    amp_ds = f.create_dataset(
        "amplitude",
        shape=(n_rows, n_out_ch),
        chunks=(chunk_rows, n_out_ch),
        dtype=np.float64,
        **compression_kwargs,
    )
    phase_ds = f.create_dataset(
        "phase",
        shape=(n_rows, n_out_ch),
        chunks=(chunk_rows, n_out_ch),
        dtype=np.float64,
        **compression_kwargs,
    )
    rec_ds = f.create_dataset(
        "recovered",
        shape=(n_rows, n_out_ch),
        chunks=(chunk_rows, n_out_ch),
        dtype=np.float64,
        **compression_kwargs,
    )
    f.attrs["lockin_real_chan_per_port"] = n_real

    # Build index arrays selecting I and Q columns across all ports
    i_cols = np.array([p * cpp + c for p in range(n_ports) for c in range(n_real)])
    q_cols = np.array(
        [p * cpp + n_real + c for p in range(n_ports) for c in range(n_real)]
    )

    _log.info("  PLL cutoff: %.1f Hz", pll_cutoff)
    n_chunks = (n_rows + chunk_rows - 1) // chunk_rows
    phi_prev: np.ndarray | None = None
    for i in range(n_chunks):
        r0 = i * chunk_rows
        r1 = min(r0 + chunk_rows, n_rows)
        raw_chunk = raw_ds[r0:r1].astype(np.float64)
        I = raw_chunk[:, i_cols]
        Q = raw_chunk[:, q_cols]
        amp_ds[r0:r1] = np.sqrt(I**2 + Q**2) * LSB
        phase_ds[r0:r1] = np.arctan2(Q, I)
        rec_chunk, phi_prev = _track_and_recover(I, Q, pll_cutoff, phi_prev)
        rec_ds[r0:r1] = rec_chunk * LSB
        if (i + 1) % 50 == 0 or i == n_chunks - 1:
            pct = 100 * (i + 1) / n_chunks
            _log.debug("  %5.1f%%  (%s / %s rows)", pct, f"{r1:,}", f"{n_rows:,}")
