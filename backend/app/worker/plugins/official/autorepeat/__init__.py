from .manifest import MANIFEST
from .plugin import AutoRepeatPlugin, _dry_run_match

PLUGIN_CLASS = AutoRepeatPlugin
MANIFEST_OBJ = MANIFEST

__all__ = ["PLUGIN_CLASS", "MANIFEST_OBJ", "_dry_run_match"]
