from .manifest import MANIFEST
from .plugin import WordGuardMiddleware

PLUGIN_CLASS = WordGuardMiddleware

__all__ = ["MANIFEST", "PLUGIN_CLASS"]
