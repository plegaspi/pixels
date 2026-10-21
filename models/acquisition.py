"""
Acquisition — unified wrapper for a timestamped capture folder.

Pass the capture folder and everything is loaded automatically from
the settings.yaml snapshot written at capture time.

    from pixels.models import Acquisition

    with Acquisition("/captures/2026-05-29-14-30-00") as acq:
        print(acq)

        if acq.has_adx134:
            v = acq.adx134.read_voltage(0, 10_000)

        if acq.has_ads1219:
            for mux, ch in acq.ads1219.channels.items():
                print(mux, ch.label, ch.voltage.mean())
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Optional

from .adx134 import ADX134Data
from .ads1219 import ADS1219Data, ADS1219Channel


class Acquisition:
    """
    Unified wrapper for a single acquisition folder.

    On construction, reads the capture-time settings.yaml to determine
    demod_mode and other hardware parameters. ADX134 and ADS1219 data
    are discovered automatically and opened lazily.

    Use as a context manager to ensure file handles are closed cleanly.
    """

    def __init__(self, path: str | Path) -> None:
        self.path      = Path(path)
        self._adx134:  Optional[ADX134Data]  = None
        self._ads1219: Optional[ADS1219Data] = None
        self._settings: Optional[dict]       = None
        self._discover()

    # ── Settings ──────────────────────────────────────────────────────────────

    @property
    def settings(self) -> dict:
        """Hardware settings written at capture time (settings.yaml), or {}."""
        if self._settings is None:
            p = self.path / "settings.yaml"
            if p.exists():
                import yaml
                self._settings = yaml.safe_load(p.read_text()) or {}
            else:
                self._settings = {}
        return self._settings

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _discover(self) -> None:
        # Pull relevant config from the capture-time snapshot
        snap_adx = self.settings.get("adx134", {})
        snap_cap = snap_adx.get("capture", {})
        mode = snap_cap.get("mode")
        demod_mode = bool(snap_cap.get("demod_mode", False))

        bins_dir = self.path / "bins"
        if bins_dir.is_dir() and (
            glob.glob(str(bins_dir / "capture_port*.h5")) or
            glob.glob(str(bins_dir / "capture_port*.bin"))
        ):
            self._adx134 = ADX134Data(bins_dir, demod_mode=demod_mode, profile=mode)

        ads_path = self.path / "ads1219.h5"
        if ads_path.exists():
            self._ads1219 = ADS1219Data(ads_path)

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def adx134(self) -> Optional[ADX134Data]:
        return self._adx134

    @property
    def ads1219(self) -> Optional[ADS1219Data]:
        return self._ads1219

    @property
    def has_adx134(self) -> bool:
        return self._adx134 is not None

    @property
    def has_ads1219(self) -> bool:
        return self._ads1219 is not None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._adx134:  self._adx134.close()
        if self._ads1219: self._ads1219.close()

    def __enter__(self):    return self
    def __exit__(self, *_): self.close()

    def __repr__(self) -> str:
        parts = []
        if self._adx134:  parts.append(repr(self._adx134))
        if self._ads1219: parts.append(repr(self._ads1219))
        body = ("\n  " + "\n  ".join(parts)) if parts else " (empty)"
        return f"Acquisition({self.path.name}){body}"
