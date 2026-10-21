import os
import socket as _socket

# Default: look for yaml files in the current working directory,
# then fall back to the bundled copies next to this file.
_BUNDLE_DIR = os.path.dirname(__file__)


def get_interface_ips() -> dict[str, str]:
    """Return {interface_name: ipv4_address} for all local interfaces."""
    try:
        import psutil
        result: dict[str, str] = {}
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == _socket.AF_INET:
                    result[name] = addr.address
                    break
        return result
    except Exception:
        pass
    try:
        import subprocess, re
        out = subprocess.check_output(
            ["ip", "-4", "addr", "show"], text=True, stderr=subprocess.DEVNULL
        )
        result = {}
        iface = None
        for line in out.splitlines():
            m = re.match(r"^\d+:\s+(\S+):", line)
            if m:
                iface = m.group(1).split("@")[0]
            m2 = re.match(r"\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m2 and iface:
                result[iface] = m2.group(1)
        return result
    except Exception:
        return {}


def _find(filename: str) -> str | None:
    # 1. CWD (e.g. running from the project root)
    cwd_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(cwd_path):
        return cwd_path
    # 2. configs/ subdirectory of CWD (project layout)
    configs_path = os.path.join(os.getcwd(), "configs", filename)
    if os.path.exists(configs_path):
        return configs_path
    # 3. Bundled defaults next to this file
    bundled = os.path.join(_BUNDLE_DIR, filename)
    if os.path.exists(bundled):
        return bundled
    return None


def load_settings_yaml() -> dict:
    path = _find("settings.yaml")
    if path is None:
        return {}
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_data_yaml() -> dict:
    """Backward-compat wrapper — returns the full settings dict."""
    return load_settings_yaml()


def load_connections_yaml() -> dict:
    """Backward-compat wrapper — returns settings['connections']['adx134'] (eth entries)."""
    return load_settings_yaml().get("connections", {}).get("adx134", {})
