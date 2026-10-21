"""Shared application state for the pixels GUI.

A single AppState instance is created at startup and passed to all widgets
that need to read or modify configuration. It is the authoritative source of
truth for the in-memory config dict and emits Qt signals whenever settings
change so that widgets can react without polling or direct coupling.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppState(QObject):
    """Observable wrapper around the runtime configuration dict.

    Signals
    -------
    settings_changed(dict)
        Emitted after any load or in-place edit; carries the full config dict.
    device_enabled_changed(str, bool)
        Emitted for each device key whose enabled state changes (from a load
        or an explicit call to set_device_enabled).
    save_dir_changed(str)
        Emitted when the save directory changes.
    """

    settings_changed       = Signal(dict)
    device_enabled_changed = Signal(str, bool)
    save_dir_changed       = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config: dict = {}
        self._settings_path: str | None = None

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_yaml(self, path: str) -> None:
        """Load a settings.yaml and emit signals for every changed device state."""
        import yaml
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        old_devs = {k: bool(v.get("enabled", True))
                    for k, v in self._config.get("devices", {}).items()}
        self._config = data
        self._settings_path = path
        self.settings_changed.emit(dict(self._config))
        new_devs = {k: bool(v.get("enabled", True))
                    for k, v in data.get("devices", {}).items()}
        for key, enabled in new_devs.items():
            if old_devs.get(key) != enabled:
                self.device_enabled_changed.emit(key, enabled)
        save_dir = data.get("save_dir", "")
        if save_dir:
            self.save_dir_changed.emit(save_dir)

    def save_yaml(self, path: str) -> None:
        """Write the current config dict to a YAML file."""
        import yaml
        with open(path, "w") as fh:
            yaml.dump(self._config, fh, default_flow_style=False, sort_keys=False)

    def load_defaults(self) -> None:
        """Load bundled defaults from pixels/configs/settings.yaml (silent on error)."""
        try:
            from pixels.configs import load_settings_yaml
            self._config = load_settings_yaml()
            self.settings_changed.emit(dict(self._config))
        except Exception:
            pass

    # ── Key access ────────────────────────────────────────────────────────────

    def get(self, *keys, default=None):
        """Read a nested value: state.get('adx134', 'capture', 'mode')."""
        node = self._config
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k)
            if node is None:
                return default
        return node

    def set(self, value, *keys) -> None:
        """Write a nested value and emit settings_changed.

        state.set("demod", "adx134", "capture", "mode")
        """
        if not keys:
            return
        node = self._config
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        self.settings_changed.emit(dict(self._config))

    def update(self, partial: dict) -> None:
        """Deep-merge partial into the config and emit settings_changed.

        Also emits device_enabled_changed for any device whose enabled flag changed.
        """
        old_devs = {k: bool(v.get("enabled", True))
                    for k, v in self._config.get("devices", {}).items()}
        _deep_merge(self._config, partial)
        self.settings_changed.emit(dict(self._config))
        new_devs = {k: bool(v.get("enabled", True))
                    for k, v in self._config.get("devices", {}).items()}
        for key, enabled in new_devs.items():
            if old_devs.get(key) != enabled:
                self.device_enabled_changed.emit(key, enabled)

    # ── Device helpers ────────────────────────────────────────────────────────

    @property
    def enabled_devices(self) -> set[str]:
        """Set of device keys that have enabled: true (or are absent from config)."""
        devs = self._config.get("devices", {})
        return {k for k, v in devs.items() if bool(v.get("enabled", True))}

    def set_device_enabled(self, key: str, enabled: bool) -> None:
        self._config.setdefault("devices", {}).setdefault(key, {})["enabled"] = enabled
        self.device_enabled_changed.emit(key, enabled)
        self.settings_changed.emit(dict(self._config))

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def connections(self) -> dict:
        return self._config.get("connections", {})

    @property
    def save_dir(self) -> str:
        return self._config.get("save_dir", "")

    @save_dir.setter
    def save_dir(self, path: str) -> None:
        self._config["save_dir"] = path
        self.save_dir_changed.emit(path)

    @property
    def config(self) -> dict:
        """The full in-memory config dict (mutable — prefer get/set methods)."""
        return self._config

    @property
    def settings_path(self) -> str | None:
        return self._settings_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
