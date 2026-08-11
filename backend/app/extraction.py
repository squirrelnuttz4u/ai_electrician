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
