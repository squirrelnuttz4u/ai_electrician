"""Thin async client for the shop's Ollama server.

All LLM traffic in the app goes through this module, so the Ollama address and
model names are configured in exactly one place (`.env`). Nothing here reaches
any external service — only the configured OLLAMA_BASE_URL.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

import httpx

from .config import settings


class OllamaError(RuntimeError):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.ollama_base_url.rstrip("/"),
        timeout=settings.ollama_timeout,
    )


async def health() -> dict:
    """Return reachability + which configured models are available."""
    try:
        async with _client() as c:
            r = await c.get("/api/tags")
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001 - surface any connection problem
        return {"reachable": False, "error": str(exc), "models": []}

    available = {m.get("name", "").split(":")[0]: m.get("name") for m in data.get("models", [])}
    available_full = {m.get("name") for m in data.get("models", [])}

    def present(model: str) -> bool:
        return model in available_full or model.split(":")[0] in available

    return {
        "reachable": True,
        "base_url": settings.ollama_base_url,
        "models": sorted(available_full),
        "configured": {
            "chat": {"name": settings.ollama_chat_model, "present": present(settings.ollama_chat_model)},
            "vision": {"name": settings.ollama_vision_model, "present": present(settings.ollama_vision_model)},
            "embed": {"name": settings.ollama_embed_model, "present": present(settings.ollama_embed_model)},
        },
    }


async def embed(text: str) -> list[float]:
    """Return an embedding vector for `text` using the configured embed model."""
    async with _client() as c:
        r = await c.post(
            "/api/embeddings",
            json={"model": settings.ollama_embed_model, "prompt": text},
        )
        if r.status_code != 200:
            raise OllamaError(f"embeddings failed [{r.status_code}]: {r.text[:300]}")
        return r.json()["embedding"]


async def generate_json(prompt: str, images: list[bytes] | None = None, model: str | None = None) -> dict:
    """Call a (vision) model and parse its response as JSON.

    Uses Ollama's `format: json` mode so the model is constrained to emit valid
    JSON. `images` are raw PNG/JPEG bytes (base64-encoded here).
    """
    payload: dict = {
        "model": model or settings.ollama_vision_model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    if images:
        payload["images"] = [base64.b64encode(img).decode("ascii") for img in images]

    async with _client() as c:
        r = await c.post("/api/generate", json=payload)
        if r.status_code != 200:
            raise OllamaError(f"generate failed [{r.status_code}]: {r.text[:300]}")
        raw = r.json().get("response", "").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"model did not return valid JSON: {raw[:300]}") from exc


async def chat_stream(
    messages: list[dict],
    model: str | None = None,
) -> AsyncIterator[str]:
    """Stream a chat completion, yielding text deltas as they arrive."""
    payload = {
        "model": model or settings.ollama_chat_model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.2},
    }
    async with _client() as c:
        async with c.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise OllamaError(f"chat failed [{r.status_code}]: {body[:300]!r}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("message", {}).get("content", "")
                if delta:
                    yield delta
                if obj.get("done"):
                    break
