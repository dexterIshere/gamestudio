"""Business layer of the studio: what its three interfaces can do.

One definition per operation, shared by the MCP server (which exposes it to an
agent), the HTTP API (which serves it to the desktop app) and the CLI. The
functions return plain data -- dicts, lists, bytes -- and know nothing of HTTP
or MCP: a caller mistake is a `ServiceError`, which each interface translates
in its own terms.
"""

from __future__ import annotations

from . import (
    briefing,
    catalog,
    connections,
    doctor,
    documents,
    folders,
    images,
    inbox,
    jobs,
    library,
    meshes,
    poses,
    produce,
    prompts,
    sheets,
    skills,
    video,
    workspace,
)
from .context import Studio, build, studio, using
from .errors import NotFound, PaymentRequired, ServiceError

__all__ = [
    "NotFound",
    "PaymentRequired",
    "ServiceError",
    "Studio",
    "briefing",
    "build",
    "catalog",
    "connections",
    "doctor",
    "documents",
    "folders",
    "images",
    "inbox",
    "jobs",
    "library",
    "meshes",
    "poses",
    "produce",
    "prompts",
    "sheets",
    "skills",
    "studio",
    "using",
    "video",
    "workspace",
]
