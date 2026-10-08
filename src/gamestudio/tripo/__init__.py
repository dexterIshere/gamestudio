"""The Tripo API, called directly: the studio's second 3D route.

Runware hosts a single Tripo model; the recent ones -- P2 and its native
quads, the H family -- are only reachable here. This package holds the HTTP
client (`v3`) and the model catalogue with prices; the `MeshProvider` seam
stays in the pipeline (see `pipeline/steps/generation.py`).
"""

from .catalog import H25, H30, H31, P1, P2
from .client import TripoClient
from .errors import TripoError, TripoTimeout

__all__ = ["H25", "H30", "H31", "P1", "P2", "TripoClient", "TripoError",
           "TripoTimeout"]
