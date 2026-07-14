"""Experimental two-dimensional constant-density acoustic FWI worker.

The package intentionally keeps the numerical implementation independent of
the C++ MCP adapter.  The adapter only has to create a whitelisted JSON config
and launch ``python -m fwi_worker``.
"""

from .config import FWIConfig, load_config, resolve_config

__all__ = ["FWIConfig", "load_config", "resolve_config"]
__version__ = "0.1.0"
