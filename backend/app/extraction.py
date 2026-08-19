"""Structured extraction of schematic entities using the vision model.

For each page we send the rendered image plus any extracted/OCR text to the
vision model and ask for a strict JSON description of the components, wires, and
connections. Everything comes back `verified=False` with a confidence score so a
tech can review and correct it later (the verify/correct loop).
"""
from __future__ import annotations

from .ollama_client import OllamaError, generate_json

EXTRACTION_PROMPT = """You are an expert industrial electrician reading a page from a
one-line / ladder electrical schematic. Extract ONLY what is actually visible on
the page. Do not invent designators, wire numbers, or voltages.

Return STRICT JSON with this exact shape:
{
  "components": [
    {"designator": "M3", "type": "motor contactor", "description": "cutter motor contactor",
     "voltage": "480V", "confidence": 0.0}
  ],
  "wires": [
    {"wire_number": "A1", "voltage": "120V", "color": "red",
     "from_ref": "CB2", "to_ref": "CR1-A1", "confidence": 0.0}
  ],
  "connections": [
    {"wire_number": "A1", "component_designator": "CR1", "terminal": "A1", "confidence": 0.0}
  ]
}

Rules:
- "designator" is the device tag exactly as printed (e.g. M3, CR1, CB2, PB1, SOL2, OL3).
- "type" is the device kind (motor, contactor, relay, breaker, fuse, push button,
  solenoid, overload, transformer, PLC, terminal, indicator light, ...).
- "wire_number" is the wire/conductor label exactly as printed (e.g. A1, 120, X2, L1).
- "voltage" only if clearly shown or unambiguous from context (e.g. control 120V, motor 480V).
- confidence is your own 0..1 certainty for each item.
- If a section has nothing, return an empty list. Output JSON only, no prose.

Text extracted from this page (may help, may be noisy):
---
{page_text}
---
"""

# Text layer here says nothing to over-rely on OCR noise; the vision model is
# authoritative for what it can see.


def _clip(text: str, limit: int = 6000) -> str:
    return text[:limit] if text else ""


async def extract_page(page_image: bytes, page_text: str) -> dict:
    """Run vision extraction for one page. Returns a dict with components/wires/
    connections lists (possibly empty). Never raises — extraction failures
    degrade to empty results so ingestion continues."""
    prompt = EXTRACTION_PROMPT.replace("{page_text}", _clip(page_text))
    try:
        data = await generate_json(prompt, images=[page_image])
    except OllamaError:
        return {"components": [], "wires": [], "connections": [], "error": True}

    return {
        "components": _as_list(data.get("components")),
        "wires": _as_list(data.get("wires")),
        "connections": _as_list(data.get("connections")),
        "error": False,
    }


def _as_list(value) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def component_summary(designator: str, type_: str | None, voltage: str | None, description: str | None) -> str:
    parts = [f"Component {designator}"]
    if type_:
        parts.append(f"({type_})")
    if voltage:
        parts.append(f"voltage {voltage}")
    if description:
        parts.append(f"- {description}")
    return " ".join(parts)


def wire_summary(wire_number: str, voltage: str | None, from_ref: str | None, to_ref: str | None) -> str:
    parts = [f"Wire {wire_number}"]
    if voltage:
        parts.append(f"carries {voltage}")
    if from_ref or to_ref:
        parts.append(f"connects {from_ref or '?'} to {to_ref or '?'}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Confidence scoring
#
# The vision model's self-reported confidence is not calibrated: on a real
# 16-page scanned drawing set it returned exactly 1.0 for two thirds of all
# components, including on pages whose OCR text was pure noise. Nothing ever
# scored below the review threshold, so no print was ever flagged and the
# correction loop never engaged.
#
# We therefore treat self-report as weak evidence and combine it with a signal
# the model cannot fabricate: whether the designator or wire number actually
# appears in the page's own text layer / OCR output.
# ---------------------------------------------------------------------------

import re

_NORMALIZE_RE = re.compile(r"[\s\-_.]+")


def _normalize(value: str | None) -> str:
    """Upper-case and strip separators so 'CR-1' matches 'CR1' and 'cr 1'."""
    return _NORMALIZE_RE.sub("", (value or "")).upper()


def corroborated(label: str | None, page_text: str | None) -> bool:
    """True when `label` literally appears in the page text.

    Independent of the model's own judgement, so it is the one piece of
    evidence that cannot be inflated by an overconfident answer. Labels shorter
    than two characters are ignored — a bare 'A' matches almost any page.
    """
    token = _normalize(label)
    if len(token) < 2:
        return False
    return token in _normalize(page_text)


def score_confidence(model_confidence, is_corroborated: bool, agreement: float = 1.0) -> float:
    """Blend self-report, text corroboration, and cross-pass agreement into 0..1.

    Calibrated against LOW_CONFIDENCE (0.55) in indexing.py so that an
    uncorroborated item always lands below the review threshold no matter how
    certain the model claims to be:

        corroborated,   model 1.0 -> 0.85    (trusted)
        corroborated,   model 0.7 -> 0.76    (trusted)
        uncorroborated, model 1.0 -> 0.50    (flagged for review)
        uncorroborated, model 0.0 -> 0.20    (flagged for review)

    `agreement` is the fraction of extraction passes that found the item; with a
    single pass it is 1.0 and has no effect.
    """
    try:
        model = max(0.0, min(1.0, float(model_confidence)))
    except (TypeError, ValueError):
        model = 0.0

    score = 0.20 + 0.30 * model
    if is_corroborated:
        score += 0.35

    # Items seen by only some passes are downweighted, never boosted.
    agreement = max(0.0, min(1.0, agreement))
    score *= 0.65 + 0.35 * agreement

    return round(max(0.0, min(1.0, score)), 3)


# ---------------------------------------------------------------------------
# Multi-pass extraction
#
# A single whole-page read has two failure modes we observed on a real scanned
# drawing set:
#   1. Under-reading — a dense sheet downscaled to the vision model's input
#      resolution loses small wire numbers, so sheets yielded 2-3 components.
#   2. Hallucination — the model saw connector blocks "J6" and "J7" and
#      generated the full pin runs J6-1..J6-14 / J7-1..J7-16 at 0.8-1.0
#      confidence. None of them appeared in the page text.
#
# Tiling addresses (1) by sending each region at full resolution. Repeated
# passes address (2): a fabricated item rarely survives an independent re-read,
# so agreement across passes is a real confidence signal, unlike self-report.
# ---------------------------------------------------------------------------

import io
from dataclasses import dataclass, field

from .config import settings


@dataclass
class MergedEntity:
    data: dict
    found_in: int = 0          # passes within its region that reported it
    region_passes: int = 1     # passes run over that region
    best_model_conf: float = 0.0

    @property
    def agreement(self) -> float:
        if self.region_passes <= 0:
            return 1.0
        return min(1.0, self.found_in / self.region_passes)


@dataclass
class MultiPassResult:
    components: list[dict] = field(default_factory=list)
    wires: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    error: bool = False
    calls_made: int = 0
    calls_failed: int = 0


def _tile_images(image: bytes, grid: int, overlap: float) -> list[bytes]:
    """Split a page image into a grid*grid set of overlapping tiles.

    Overlap keeps entities that straddle a tile boundary readable in at least
    one tile. Returns [image] unchanged when grid <= 1 or Pillow is unavailable.
    """
    if grid <= 1:
        return [image]
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - tiling is an enhancement, never fatal
        return [image]

    try:
        with Image.open(io.BytesIO(image)) as im:
            im = im.convert("RGB")
            w, h = im.size
            tw, th = w / grid, h / grid
            ox, oy = tw * overlap, th * overlap
            tiles: list[bytes] = []
            for row in range(grid):
                for col in range(grid):
                    left = max(0, int(col * tw - ox))
                    upper = max(0, int(row * th - oy))
                    right = min(w, int((col + 1) * tw + ox))
                    lower = min(h, int((row + 1) * th + oy))
                    buf = io.BytesIO()
                    im.crop((left, upper, right, lower)).save(buf, format="PNG")
                    tiles.append(buf.getvalue())
            return tiles
    except Exception:  # noqa: BLE001
        return [image]


def _key(value) -> str:
    return _normalize(str(value or ""))


def _merge(store: dict[str, MergedEntity], items: list[dict], key_field: str, region_passes: int) -> None:
    """Fold one pass's items into the running store, tracking how often each key
    was seen so agreement can be computed later."""
    seen_this_pass: set[str] = set()
    for item in items:
        k = _key(item.get(key_field))
        if not k or k in seen_this_pass:
            continue  # ignore duplicates inside a single pass
        seen_this_pass.add(k)

        try:
            conf = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0

        existing = store.get(k)
        if existing is None:
            store[k] = MergedEntity(data=dict(item), found_in=1,
                                    region_passes=region_passes, best_model_conf=conf)
            continue

        existing.found_in += 1
        existing.region_passes = max(existing.region_passes, region_passes)
        # Keep the richest description: prefer the reading that was most certain,
        # then fill any field the winner left blank.
        if conf > existing.best_model_conf:
            merged = dict(item)
            for f, v in existing.data.items():
                if merged.get(f) in (None, "") and v not in (None, ""):
                    merged[f] = v
            existing.data = merged
            existing.best_model_conf = conf
        else:
            for f, v in item.items():
                if existing.data.get(f) in (None, "") and v not in (None, ""):
                    existing.data[f] = v


async def extract_page_multi(
    page_image: bytes,
    page_text: str,
    passes: int | None = None,
    tile_grid: int | None = None,
) -> MultiPassResult:
    """Read one page with `passes` reads over each of tile_grid^2 regions.

    Each returned item carries an "agreement" field (0..1): the fraction of
    passes over its own region that reported it. Never raises; if every call
    fails the result is empty with error=True.
    """
    passes = max(1, passes if passes is not None else settings.extraction_passes)
    grid = max(1, tile_grid if tile_grid is not None else settings.extraction_tile_grid)

    regions = _tile_images(page_image, grid, settings.extraction_tile_overlap)

    comps: dict[str, MergedEntity] = {}
    wires: dict[str, MergedEntity] = {}
    conns: dict[str, MergedEntity] = {}
    calls = failed = 0

    for region in regions:
        for _ in range(passes):
            calls += 1
            single = await extract_page(region, page_text)
            if single.get("error"):
                failed += 1
                continue
            _merge(comps, single["components"], "designator", passes)
            _merge(wires, single["wires"], "wire_number", passes)
            for c in single["connections"]:
                k = f"{_key(c.get('wire_number'))}|{_key(c.get('component_designator'))}"
                if not k.strip("|"):
                    continue
                if k in conns:
                    conns[k].found_in += 1
                else:
                    conns[k] = MergedEntity(data=dict(c), found_in=1, region_passes=passes)

    def flatten(store: dict[str, MergedEntity]) -> list[dict]:
        out = []
        for e in store.values():
            d = dict(e.data)
            d["agreement"] = round(e.agreement, 3)
            out.append(d)
        return out

    return MultiPassResult(
        components=flatten(comps),
        wires=flatten(wires),
        connections=flatten(conns),
        error=(failed == calls and calls > 0),
        calls_made=calls,
        calls_failed=failed,
    )
