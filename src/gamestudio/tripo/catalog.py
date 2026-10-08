"""Tripo models, and what a call really costs.

The studio buys its meshes from Runware, which hosts a single Tripo model
(v3.1). The recent models -- P2 with its native quads, and the H family -- are
only reachable by calling the Tripo API directly. This catalogue lists what
that route offers, with its price, so that the interface and agents see what
they pay before clicking (`service/meshes.py`).

**The price is not a list price**: Tripo bills in credits ($0.01 per credit)
and the spending depends on the model and the requested options. The numbers
below are therefore *computed* for the request the studio actually sends
(reference image, texture, PBR, face budget): the model base, standard or
detailed texture, plus the geometry-quality and quad surcharges. A model
announced at $0.40 and billed $1.20 would mean spending blind. The real cost
of a task is the one Tripo returns (`credits_consumed`); this computation is
the announcement, and the fallback when the response does not say.

Identifiers are those of the Tripo documentation (`model=`): canonical names
(`tripo-p1`, `tripo-v3.1`) for generation, and dated snapshots
(`P2-20260801`) when they exist -- a pinned snapshot keeps a render
reproducible from one month to the next.
"""

from __future__ import annotations

from typing import Any

# --- generation models -------------------------------------------------------
# P Series: topology is the point. P1 holds 48 to 20,000 faces and returns in
# ~10 s; P2 is the only one producing native quads, up to 50,000 triangles /
# 25,000 quads. Both output a textured GLB.
P2 = "P2-20260801"
P1 = "P1-20260311"
# H Series: fidelity is the point, topology is adaptive. Quads force an FBX
# there (the studio wants a GLB): P2 is the quad model.
H31 = "tripo-v3.1"
H30 = "tripo-v3.0"
H25 = "tripo-v2.5"

# --- prices -------------------------------------------------------------------
# 1 credit = $0.01. Tripo's grid for an image -> 3D request: a base per model
# (untextured), then the texture -- standard +10, detailed +20, extreme +30 --,
# then the H family surcharges: detailed geometry +20, quads +5. The studio's
# default request (standard texture) therefore costs 110 credits on P2, 50 on
# P1, 30 on H3.1. The front shows the same prices (`app/src/lib/catalog.ts`,
# kept equal by `test_mesh_catalog`).
#
# Sources (2026-10-07): https://developers.tripo3d.com/en/pricing, models/p1, models/v3-1, changelog
#
# Two gaps in the documentation, filled without guessing low:
# - P2 quads: the grid prices quads (+5) only for the H family, the P page is
#   silent. They are counted: announcing $0.05 too much beats charging more
#   than announced;
# - H3.0 and H2.5 have no price of their own: they follow the H grid.
USD_PER_CREDIT = 0.01
IMAGE_BASE_CREDITS: dict[str, float] = {
    P2: 100.0,
    P1: 40.0,
    H31: 20.0,
    H30: 20.0,
    H25: 20.0,
}
# `fast` costs the same as `standard` (Tripo changelog, v3.5 textures).
TEXTURE_QUALITY_CREDITS = {"fast": 10.0, "standard": 10.0, "detailed": 20.0,
                           "extreme": 30.0}
GEOMETRY_DETAILED_CREDITS = 20.0
QUAD_CREDITS = 5.0

# The `face_limit` bounds per model, as documented. The studio asks for 8,000
# faces by default; a model that cannot go that low must say so, not return
# something other than what was asked.
FACE_LIMITS: dict[str, tuple[int, int]] = {
    P1: (48, 20_000),
    P2: (48, 50_000),
    H31: (48, 2_000_000),
    H30: (48, 1_000_000),
    H25: (48, 500_000),
}
# In quad mode, P2 counts quads, and holds half as many.
QUAD_FACE_LIMITS: dict[str, tuple[int, int]] = {
    P2: (48, 25_000),
}

# What each model can do. `quad` is True (native quads, as GLB), "fbx" (quads
# forced to FBX, which the studio cannot render to sprites) or False:
# `TripoMesh` refuses quads in the last two cases instead of ignoring them.
CAPABILITIES: dict[str, dict[str, Any]] = {
    P1: {"quad": False, "smart_low_poly": False, "geometry_quality": False,
         "texture_version": "v3.0-20250812"},
    P2: {"quad": True, "smart_low_poly": False, "geometry_quality": False,
         "texture_version": "v3.0-20250812"},
    H31: {"quad": "fbx", "smart_low_poly": True, "geometry_quality": True,
          "texture_version": "v3.0-20250812"},
    H30: {"quad": "fbx", "smart_low_poly": True, "geometry_quality": True,
          "texture_version": "v3.0-20250812"},
    H25: {"quad": "fbx", "smart_low_poly": False, "geometry_quality": False,
          "texture_version": "v2.5-20250123"},
}


def supports(model: str, capability: str) -> Any:
    """What the model offers for the requested capability, or False."""
    return CAPABILITIES.get(model, {}).get(capability, False)


def face_limits(model: str, *, quad: bool = False) -> tuple[int, int]:
    """The model's face bounds; P2's if the model is unknown.

    `quad` gives the bounds of a quad output, when the model has different
    ones. An unknown model is refused by the API, not by this function: it
    bounds a request, it does not validate an identifier.
    """
    if quad and model in QUAD_FACE_LIMITS:
        return QUAD_FACE_LIMITS[model]
    return FACE_LIMITS.get(model, FACE_LIMITS[P2])


def clamp_face_limit(model: str, wanted: int, *, quad: bool = False) -> int:
    """Clamp a face budget to what the model accepts."""
    low, high = face_limits(model, quad=quad)
    return max(low, min(high, int(wanted)))


def credits_for(model: str, *, texture_quality: str = "standard",
                geometry_quality: str = "standard", quad: bool = False) -> float:
    """The credits a textured image → 3D call costs, options included.

    A model missing from the grid is counted at the most expensive base: an
    announcement too high is corrected by the bill, one too low gets a
    spending accepted that nobody saw.
    """
    base = IMAGE_BASE_CREDITS.get(model, max(IMAGE_BASE_CREDITS.values()))
    texture = TEXTURE_QUALITY_CREDITS.get(texture_quality,
                                          TEXTURE_QUALITY_CREDITS["standard"])
    credits = base + texture
    if geometry_quality == "detailed" and supports(model, "geometry_quality"):
        credits += GEOMETRY_DETAILED_CREDITS
    if quad and supports(model, "quad") is True:
        credits += QUAD_CREDITS
    return credits


def usd_for(model: str, *, texture_quality: str = "standard",
            geometry_quality: str = "standard", quad: bool = False) -> float:
    """The price in dollars of what the studio sends for this model."""
    return round(credits_for(model, texture_quality=texture_quality,
                             geometry_quality=geometry_quality, quad=quad)
                 * USD_PER_CREDIT, 4)


def models() -> list[dict[str, Any]]:
    """The direct route's catalogue, from the most game-ready to the most faithful.

    `cost_usd` is the price of the studio's default request (standard
    texture, no quads); `cost_detailed_usd` that of the same request in
    detailed quality, which `detailed=True` asks for -- texture, and geometry
    where the model supports it. P2 quads add `QUAD_CREDITS` to both.
    """
    return [
        {
            "id": P2,
            "label": "Tripo P2",
            "note": "Native quads (25,000 faces at most, 50,000 as triangles): the only 3D model "
                    "that outputs game-ready topology without a retopology pass. Comes out as "
                    "GLB, quads included; the most expensive on the direct path, quads +$0.05.",
            "cost_usd": usd_for(P2),
            "cost_detailed_usd": usd_for(P2, texture_quality="detailed"),
            "faces": list(face_limits(P2)),
            "quad": True,
        },
        {
            "id": P1,
            "label": "Tripo P1",
            "note": "Strict low-poly (48-20,000 faces), base mesh in ~10 s: for iterating on a "
                    "game silhouette.",
            "cost_usd": usd_for(P1),
            "cost_detailed_usd": usd_for(P1, texture_quality="detailed"),
            "faces": list(face_limits(P1)),
            "quad": False,
        },
        {
            "id": H31,
            "label": "Tripo H3.1",
            "note": "Tripo's high-fidelity model, the one Runware also resells, and the cheapest "
                    "on the direct path: take it here when texture matters, not topology.",
            "cost_usd": usd_for(H31),
            "cost_detailed_usd": usd_for(H31, texture_quality="detailed",
                                         geometry_quality="detailed"),
            "faces": list(face_limits(H31)),
            "quad": "FBX",
        },
    ]
