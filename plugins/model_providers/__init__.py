"""Import shim for bundled plugins stored under ``model-providers``."""

from pathlib import Path


__path__ = [str(Path(__file__).resolve().parent.parent / "model-providers")]
