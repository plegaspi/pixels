from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Data streams per board.  The FPGA sends frame k of all N_STREAMS streams
# back to back in one UDP datagram (each sub-frame keeps its own header word);
# the host splits it into one spool file per stream.
N_STREAMS = 4


@dataclass(frozen=True)
class ADX134Profile:
    mode: str
    n_chan_per_port: int
    samples_per_frame: int
    payload_bytes: int
    demod_decimation: int

    @property
    def demod_mode(self) -> bool:
        return self.mode == "demod"

    @property
    def n_words(self) -> int:
        return self.payload_bytes // 4

    @property
    def datagram_bytes(self) -> int:
        """UDP payload size of one datagram: N_STREAMS sub-frames back to back."""
        return N_STREAMS * self.payload_bytes

    def attrs(self, odr_hz: float | None = None) -> dict[str, Any]:
        sample_rate_hz = None
        if odr_hz is not None:
            sample_rate_hz = odr_hz / max(1, self.demod_decimation)
        return {
            "capture_mode": self.mode,
            "demod_mode": self.demod_mode,
            "n_chan_per_port": self.n_chan_per_port,
            "samples_per_frame": self.samples_per_frame,
            "payload_bytes": self.payload_bytes,
            "n_words": self.n_words,
            "demod_decimation": self.demod_decimation,
            "odr_hz": odr_hz if odr_hz is not None else 0.0,
            "sample_rate_hz": sample_rate_hz if sample_rate_hz is not None else 0.0,
        }

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


RAW_PROFILE = ADX134Profile(
    mode="raw",
    n_chan_per_port=28,
    samples_per_frame=11,
    payload_bytes=928,
    demod_decimation=1,
)

DEMOD_PROFILE = ADX134Profile(
    mode="demod",
    n_chan_per_port=56,
    samples_per_frame=8,
    payload_bytes=1348,
    demod_decimation=4,
)

PROFILES = {
    RAW_PROFILE.mode: RAW_PROFILE,
    DEMOD_PROFILE.mode: DEMOD_PROFILE,
}

# Largest datagram any profile produces (receive buffer size).
MAX_DATAGRAM_BYTES = max(p.datagram_bytes for p in PROFILES.values())


def normalize_capture_mode(mode: str | None = None, demod_mode: bool | None = None) -> str:
    if mode is None:
        return "demod" if demod_mode else "raw"
    if isinstance(mode, bytes):
        mode = mode.decode(errors="ignore")
    normalized = str(mode).strip().lower()
    aliases = {
        "iq": "demod",
        "i/q": "demod",
        "demodulation": "demod",
        "demodulated": "demod",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in PROFILES:
        return "demod" if demod_mode else "raw"
    return normalized


def get_adx134_profile(mode: str | None = None, demod_mode: bool | None = None) -> ADX134Profile:
    return PROFILES[normalize_capture_mode(mode, demod_mode)]


def profile_from_config(config: dict[str, Any] | None) -> ADX134Profile:
    config = config or {}
    capture = config.get("adx134", {}).get("capture", config)
    mode = capture.get("mode")
    demod = capture.get("demod_mode")
    return get_adx134_profile(mode, bool(demod) if demod is not None else None)


def profile_from_attrs(attrs: Any, fallback: ADX134Profile | None = None) -> ADX134Profile:
    fallback = fallback or RAW_PROFILE
    try:
        if "capture_mode" in attrs:
            return get_adx134_profile(str(attrs["capture_mode"]))
        if "demod_mode" in attrs:
            return get_adx134_profile(demod_mode=bool(attrs["demod_mode"]))
        payload = int(attrs.get("payload_bytes", 0))
    except Exception:
        return fallback
    for profile in PROFILES.values():
        if payload == profile.payload_bytes:
            return profile
    return fallback
