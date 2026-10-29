from .manifest import MANIFEST
from .plugin import SignatureMiddleware

PLUGIN_CLASS = SignatureMiddleware

__all__ = ["MANIFEST", "PLUGIN_CLASS"]
