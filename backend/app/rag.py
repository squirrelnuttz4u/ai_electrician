"""Retrieval for troubleshooting: hybrid semantic + keyword search over a
machine's prints, plus verified knowledge-base entries.

Wire numbers and designators (A1, M3, CB2) must match *exactly*, so we combine
pgvector semantic similarity with Postgres full-text + trigram-ish ILIKE keyword
matching. Retrieval is always scoped to one machine.
"""
from __future__ import annotations

import re

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from . import ollama_client
from .models import DocChunk, Print, TroubleshootingKB

# tokens that look like wire numbers / designators, e.g. A1, M3, CB2, 120, X2, L1
_TOKEN_RE = re.compile(r"\b([A-Z]{1,3}\d{1,4}|\d{2,4}[A-Z]?|[A-Z]\d[A-Z]?)\b")


def extract_tokens(question: str) -> list[str]:
    return list({m.group(1) for m in _TOKEN_RE.finditer(question.upper())})


async def retrieve(
    db: Session,
    machine_id: int,
    question: str,
    k_semantic: int = 8,
    k_keyword: int = 6,
) -> list[dict]:
    """Return a de-duplicated list of context chunks for the question."""
    hits: dict[int, dict] = {}
    titles = {p.id: p.title for p in db.query(Print).filter(Print.machine_id == machine_id).all()}

    # --- semantic search via pgvector (cosine distance) ---
    try:
        qvec = await ollama_client.embed(question)
        rows = db.execute(
            sql_text(
                """
                SELECT id, print_id, page_number, kind, content,
                       1 - (embedding <=> CAST(:qvec AS vector)) AS score
                FROM doc_chunks
                WHERE machine_id = :mid AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
            ),
            {"qvec": str(qvec), "mid": machine_id, "k": k_semantic},
        ).mappings()
        for r in rows:
            hits[r["id"]] = {**dict(r), "match": "semantic"}
    except Exception:  # noqa: BLE001 - embeddings/pgvector unavailable -> keyword only
        pass

    # --- keyword search (exact wire numbers / designators + full text) ---
    tokens = extract_tokens(question)
    if tokens:
        like_clauses = " OR ".join([f"content ILIKE :t{i}" for i in range(len(tokens))])
        params = {f"t{i}": f"%{tok}%" for i, tok in enumerate(tokens)}
        params.update({"mid": machine_id, "k": k_keyword})
        rows = db.execute(
            sql_text(
                f"""
                SELECT id, print_id, page_number, kind, content, 1.0 AS score
                FROM doc_chunks
                WHERE machine_id = :mid AND ({like_clauses})
                LIMIT :k
                """
            ),
            params,
        ).mappings()
        for r in rows:
            hits.setdefault(r["id"], {**dict(r), "match": "keyword"})

    # attach print titles + snippet
    out = []
    for h in hits.values():
        out.append(
            {
                "chunk_id": h["id"],
                "print_id": h["print_id"],
                "print_title": titles.get(h["print_id"], "print"),
                "page_number": h["page_number"],
                "kind": h["kind"],
                "content": h["content"],
                "score": float(h["score"]),
                "match": h["match"],
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[: k_semantic + k_keyword]


async def retrieve_kb(db: Session, machine_id: int, question: str, k: int = 4) -> list[TroubleshootingKB]:
    """Retrieve verified KB entries most relevant to the question."""
    try:
        qvec = await ollama_client.embed(question)
        rows = db.execute(
            sql_text(
                """
                SELECT id
                FROM troubleshooting_kb
                WHERE machine_id = :mid AND verified = true AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
            ),
            {"qvec": str(qvec), "mid": machine_id, "k": k},
        ).mappings()
        ids = [r["id"] for r in rows]
    except Exception:  # noqa: BLE001
        ids = []

    if not ids:
        # fall back to most recent verified entries
        return (
            db.query(TroubleshootingKB)
            .filter(TroubleshootingKB.machine_id == machine_id, TroubleshootingKB.verified.is_(True))
            .order_by(TroubleshootingKB.created_at.desc())
            .limit(k)
            .all()
        )
    order = {cid: i for i, cid in enumerate(ids)}
    entries = db.query(TroubleshootingKB).filter(TroubleshootingKB.id.in_(ids)).all()
    entries.sort(key=lambda e: order.get(e.id, 999))
    return entries


SYSTEM_PROMPT = """You are AI Electrician, an expert industrial-controls troubleshooting
assistant. A maintenance technician describes a symptom; you give specific, safe,
step-by-step diagnostic guidance.

STRICT GROUNDING RULES:
- Use ONLY the wire numbers, component designators, terminals, and voltages that
  appear in the CONTEXT below. Never invent a wire number or designator.
- If the context lacks the detail needed, say exactly what to look for on the print
  and which print/page to open — do not guess specifics.
- Prefer concrete checks: "measure 120V between wire A1 and ground", "confirm
  contactor M3 is pulled in", "check for continuity across overload OL3".
- Always lead with safety (verify lockout/PPE) when advising live measurements.
- Cite the print title and page for each fact you rely on, inline like [Print: <title> p<n>].
- Be concise and ordered: most-likely / easiest-to-check first.

Verified knowledge base entries (authoritative — trust these over your own guesses):
{kb}

CONTEXT (facts extracted from this machine's prints):
{context}
"""


def build_messages(question: str, context_chunks: list[dict], kb_entries: list[TroubleshootingKB], history: list[dict] | None = None) -> list[dict]:
    ctx_lines = []
    for c in context_chunks:
        loc = f"[{c['print_title']} p{c['page_number']}]" if c.get("page_number") else f"[{c['print_title']}]"
        ctx_lines.append(f"{loc} {c['content']}")
    context = "\n".join(ctx_lines) if ctx_lines else "(no matching print context found)"

    if kb_entries:
        kb = "\n".join(f"- SYMPTOM: {e.symptom}\n  GUIDANCE: {e.guidance}" for e in kb_entries)
    else:
        kb = "(none yet)"

    system = SYSTEM_PROMPT.replace("{kb}", kb).replace("{context}", context)
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages
