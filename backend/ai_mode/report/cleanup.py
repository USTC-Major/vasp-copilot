"""Compatibility import; implementation belongs to Toolbox."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module('backend.toolbox.report.cleanup')
