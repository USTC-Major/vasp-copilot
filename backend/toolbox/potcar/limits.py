"""Central resource limits; callers may inject smaller limits for isolated tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_input: int = 8 * 1024 * 1024
    max_output: int = 16 * 1024 * 1024
    decode_timeout: float = 10.0
    max_candidates: int = 10_000
    max_depth: int = 6
    scan_timeout: float = 120.0
    max_entries: int = 100_000
    max_directories: int = 20_000
    max_parallel_scans: int = 2
    max_assembly: int = 32 * 1024 * 1024
    max_previews: int = 128
    max_species_blocks: int = 256
    max_preview_candidates: int = 10_000
    preview_ttl: int = 30 * 60
    artifact_ttl: int = 24 * 60 * 60

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError('POTCAR limits must be positive')
