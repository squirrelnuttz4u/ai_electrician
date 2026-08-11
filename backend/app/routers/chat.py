"""Troubleshooting chat — the tech-facing assistant.

Flow per question:
  1. resolve/create a machine-scoped session and store the user message
  2. retrieve grounded context (prints) + verified KB entries for the machine
  3. stream the model's answer back to the browser as it is generated
  4. persist the assistant message with citations to the exact prints/pages used

Feedback (good/bad + optional corrected guidance) feeds the KB so the assistant
improves per machine over time without retraining any model weights.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import ollama_client, rag
from ..auth import require_auth
from ..database import SessionLocal, get_db
from ..models import ChatMessage, ChatSession, Machine, TroubleshootingKB
from ..schemas import ChatAsk, ChatMessageOut, ChatSessionOut, FeedbackIn, KBEntryOut

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(machine_id: int, db: Session = Depends(get_db)):
    return (
        db.query(ChatSession)
        .filter(ChatSession.machine_id == machine_id)
        .order_by(ChatSession.created_at.desc())
        .all()
    )


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def list_messages(session_id: int, db: Session = Depends(get_db)):
    if not db.get(ChatSession, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
        .all()
    )


def _history(db: Session, session_id: int, limit: int = 6) -> list[dict]:
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


@router.post("/ask")
async def ask(payload: ChatAsk, db: Session = Depends(get_db)):
    machine = db.get(Machine, payload.machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # resolve or create the session
    session = db.get(ChatSession, payload.session_id) if payload.session_id else None
    if session is None:
        session = ChatSession(machine_id=payload.machine_id, title=payload.message[:80])
        db.add(session)
        db.commit()
        db.refresh(session)

    db.add(ChatMessage(session_id=session.id, role="user", content=payload.message))
    db.commit()

    history = _history(db, session.id)[:-1]  # exclude the just-added user turn

    # retrieval (grounding)
    context = await rag.retrieve(db, payload.machine_id, payload.message)
    kb = await rag.retrieve_kb(db, payload.machine_id, payload.message)
    messages = rag.build_messages(payload.message, context, kb, history=history)

    citations = [
        {
            "print_id": c["print_id"],
            "print_title": c["print_title"],
            "page_number": c["page_number"],
            "kind": c["kind"],
            "snippet": c["content"][:200],
        }
        for c in context
    ]
    session_id = session.id

    async def stream():
        # first frame: metadata (session id + citations) so the UI can render links
        yield json.dumps({"type": "meta", "session_id": session_id, "citations": citations}) + "\n"
        parts: list[str] = []
        try:
            async for delta in ollama_client.chat_stream(messages):
                parts.append(delta)
                yield json.dumps({"type": "delta", "text": delta}) + "\n"
        except ollama_client.OllamaError as exc:
            err = f"\n\n[Error contacting the LLM: {exc}]"
            parts.append(err)
            yield json.dumps({"type": "delta", "text": err}) + "\n"

        answer = "".join(parts).strip()
        # persist the assistant message in a fresh session (the request-scoped
        # one is closed once the response starts streaming)
        wdb = SessionLocal()
        try:
            msg = ChatMessage(session_id=session_id, role="assistant", content=answer, citations=citations)
            wdb.add(msg)
            wdb.commit()
            wdb.refresh(msg)
            yield json.dumps({"type": "done", "message_id": msg.id}) + "\n"
        finally:
            wdb.close()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/messages/{message_id}/feedback", response_model=KBEntryOut | None)
def feedback(message_id: int, payload: FeedbackIn, db: Session = Depends(get_db)):
    """Record thumbs up/down. A confirmed-good answer, or a supplied correction,
    is saved to the machine's knowledge base and retrieved on future questions."""
    msg = db.get(ChatMessage, message_id)
    if not msg or msg.role != "assistant":
        raise HTTPException(status_code=404, detail="Assistant message not found")
    msg.feedback = "good" if payload.feedback == "good" else "bad"

    session = db.get(ChatSession, msg.session_id)
    # the user turn immediately preceding this answer is the symptom
    prior_user = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == msg.session_id, ChatMessage.id < msg.id, ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .first()
    )
    symptom = prior_user.content if prior_user else (session.title or "")
    guidance = payload.corrected_guidance or (msg.content if msg.feedback == "good" else None)

    entry = None
    if guidance and symptom:
        from ..routers.review import _embed_sync

        entry = TroubleshootingKB(
            machine_id=session.machine_id,
            symptom=symptom,
            guidance=guidance,
            source="confirmed_answer" if msg.feedback == "good" else "tech",
            verified=True,
            embedding=_embed_sync(f"{symptom}\n{guidance}"),
        )
        db.add(entry)
    db.commit()
    if entry:
        db.refresh(entry)
    return entry
