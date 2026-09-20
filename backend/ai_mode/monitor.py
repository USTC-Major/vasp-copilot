"""Compatibility import. The AI server never starts this monitor."""
import importlib, sys
sys.modules[__name__] = importlib.import_module("backend.toolbox.monitor")
