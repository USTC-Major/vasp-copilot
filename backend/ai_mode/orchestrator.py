"""Compatibility import for deterministic execution tests."""
import importlib, sys
sys.modules[__name__] = importlib.import_module("backend.toolbox.orchestrator")
