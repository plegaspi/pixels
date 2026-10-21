"""Package-level logging helper.

Usage in any pixels module:
    from pixels._log import get_logger
    _log = get_logger(__name__)
    _log.info("...")
    _log.debug("...")
"""

import logging

# Null handler — package is silent unless the caller installs a handler.
logging.getLogger("pixels").addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the 'pixels' hierarchy."""
    if not name.startswith("pixels"):
        name = f"pixels.{name}"
    return logging.getLogger(name)
