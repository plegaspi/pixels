"""RFSoC protocol parsing — frame header decoding and sample extraction."""

from __future__ import annotations

import numpy as np

from .constants import (
    CHANNEL_LABELS,
    TCP_MAGIC_V1, TCP_MAGIC_V2, TCP_MAGIC_V3,
    UDP_MAGIC_V1, UDP_MAGIC_V2, UDP_MAGIC_V3,
)

__all__ = [
    "recv_exact",
    "channel_count_from_header",
    "payload_to_samples",
    "apply_lane_order",
    "select_word_lane",
]

_CHANNEL_LABELS = CHANNEL_LABELS


def recv_exact(sock, size: int) -> bytes:
    """Read exactly `size` bytes from a TCP socket, blocking until complete."""
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def channel_count_from_header(magic: bytes, version: int, mode: str) -> int:
    """Return the channel count encoded in a frame header magic, or 0 if invalid."""
    if mode == "tcp":
        if magic == TCP_MAGIC_V1 and version == 1:
            return 1
        if magic == TCP_MAGIC_V2 and version == 2:
            return 2
        if magic == TCP_MAGIC_V3 and version == 3:
            return 4
    else:
        if magic == UDP_MAGIC_V1 and version == 1:
            return 1
        if magic == UDP_MAGIC_V2 and version == 2:
            return 2
        if magic == UDP_MAGIC_V3 and version == 3:
            return 4
    return 0


def payload_to_samples(payload: bytes, channel_count: int) -> np.ndarray:
    """
    Deserialise a raw payload into a signed-16-bit sample array.

    Returns (n_samples, channel_count) int16 for multi-channel,
    or (n_samples,) int16 for single channel.
    """
    samples = np.frombuffer(payload, dtype="<i2").copy()
    if channel_count > 1:
        return samples.reshape((-1, channel_count))
    return samples


def apply_lane_order(
    samples: np.ndarray,
    lane_order: str,
    lanes_per_word: int,
) -> np.ndarray:
    """Reorder 16-bit lanes within each RFDC AXIS word if msb-first is selected."""
    if lane_order == "lsb-first":
        return samples

    n = samples.shape[0]
    aligned = n - (n % lanes_per_word)
    if aligned == 0:
        return samples

    head = samples[:aligned]
    tail = samples[aligned:]

    if samples.ndim == 1:
        reordered = head.reshape((-1, lanes_per_word))[:, ::-1].reshape((-1,))
    else:
        reordered = (
            head.reshape((-1, lanes_per_word, samples.shape[1]))[:, ::-1, :]
            .reshape((-1, samples.shape[1]))
        )

    if tail.size == 0:
        return reordered
    return np.concatenate((reordered, tail), axis=0)


def select_word_lane(
    samples: np.ndarray,
    lane: int | None,
    lanes_per_word: int,
) -> np.ndarray:
    """Keep only one 16-bit lane from each RFDC AXIS word (downsamples by lanes_per_word)."""
    if lane is None:
        return samples
    n = samples.shape[0]
    aligned = n - (n % lanes_per_word)
    if aligned == 0:
        return samples[:0]
    return samples[lane:aligned:lanes_per_word]
