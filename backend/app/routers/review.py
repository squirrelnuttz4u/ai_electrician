"""Verify & correct loop.

Techs correct extracted components/wires (which immediately improves the facts
the AI reasons over) and manage the per-machine troubleshooting knowledge base.
Every correction is recorded in `corrections` for auditability and future use.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..extraction import component_summary, wire_summary
from ..models import Component, Correction, DocChunk, TroubleshootingKB, Wire
from ..ollama_client import embed
from ..schemas import (
    ComponentOut,
    ComponentUpdate,
    KBEntryIn,
    KBEntryOut,
    WireOut,
    WireUpdate,
)

router = APIRouter(prefix="/api", tags=["review"], dependencies=[Depends(require_auth)])


def _embed_sync(text_value: str):
    try:
        return asyncio.run(embed(text_value))
    except Exception:  # noqa: BLE001
        return None


def _refresh_chunk(db: Session, print_id: int, page: int, kind: str, source_id: int, content: str) -> None:
    """Keep the searchable chunk in sync with a corrected entity (upsert by
    source_id, so the old text is replaced rather than duplicated)."""
    chunk = (
        db.query(DocChunk)
        .filter(DocChunk.kind == kind, DocChunk.source_id == source_id)
        .first()
    )
    embedding = _embed_sync(content)
    if chunk is None:
        db.add(DocChunk(print_id=print_id, machine_id=_machine_of(db, print_id),
                        page_number=page, kind=kind, source_id=source_id,
                        content=content, embedding=embedding))
    else:
        chunk.content = content
        chunk.embedding = embedding


def _machine_of(db: Session, print_id: int) -> int:
    from ..models import Print
    pr = db.get(Print, print_id)
    return pr.machine_id if pr else 0


@router.patch("/components/{component_id}", response_model=ComponentOut)
def update_component(component_id: int, payload: ComponentUpdate, db: Session = Depends(get_db)):
    c = db.get(Component, component_id)
    if not c:
        raise HTTPException(status_code=404, detail="Component not found")
    original = {"designator": c.designator, "type": c.type, "voltage": c.voltage, "description": c.description}

    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    for k, v in data.items():
        setattr(c, k, v)
    if "verified" not in data:
        c.verified = True  # a manual edit counts as verification
    c.confidence = max(c.confidence, 0.99)

    db.add(Correction(target_type="component", target_id=c.id, print_id=c.print_id,
                       machine_id=_machine_of(db, c.print_id), original=original,
                       corrected={"designator": c.designator, "type": c.type, "voltage": c.voltage,
                                  "description": c.description}, note=note))
    _refresh_chunk(db, c.print_id, c.page_number, "component", c.id,
                   component_summary(c.designator, c.type, c.voltage, c.description))
    db.commit()
    db.refresh(c)
    return c


@router.patch("/wires/{wire_id}", response_model=WireOut)
def update_wire(wire_id: int, payload: WireUpdate, db: Session = Depends(get_db)):
    w = db.get(Wire, wire_id)
    if not w:
        raise HTTPException(status_code=404, detail="Wire not found")
    original = {"wire_number": w.wire_number, "voltage": w.voltage, "from_ref": w.from_ref, "to_ref": w.to_ref}

    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    for k, v in data.items():
        setattr(w, k, v)
    if "verified" not in data:
        w.verified = True
    w.confidence = max(w.confidence, 0.99)

    db.add(Correction(target_type="wire", target_id=w.id, print_id=w.print_id,
                      machine_id=_machine_of(db, w.print_id), original=original,
                      corrected={"wire_number": w.wire_number, "voltage": w.voltage,
                                 "from_ref": w.from_ref, "to_ref": w.to_ref}, note=note))
    _refresh_chunk(db, w.print_id, w.page_number, "wire", w.id,
                   wire_summary(w.wire_number, w.voltage, w.from_ref, w.to_ref))
    db.commit()
    db.refresh(w)
    return w


@router.delete("/components/{component_id}", status_code=204)
def delete_component(component_id: int, db: Session = Depends(get_db)):
    c = db.get(Component, component_id)
    if not c:
        raise HTTPException(status_code=404, detail="Component not found")
    db.delete(c)
    db.commit()


@router.delete("/wires/{wire_id}", status_code=204)
def delete_wire(wire_id: int, db: Session = Depends(get_db)):
    w = db.get(Wire, wire_id)
    if not w:
        raise HTTPException(status_code=404, detail="Wire not found")
    db.delete(w)
    db.commit()


# ---------- Troubleshooting knowledge base ----------
@router.get("/machines/{machine_id}/kb", response_model=list[KBEntryOut])
def list_kb(machine_id: int, db: Session = Depends(get_db)):
    return (
        db.query(TroubleshootingKB)
        .filter(TroubleshootingKB.machine_id == machine_id)
        .order_by(TroubleshootingKB.created_at.desc())
        .all()
    )


@router.post("/kb", response_model=KBEntryOut, status_code=201)
def create_kb(payload: KBEntryIn, db: Session = Depends(get_db)):
    entry = TroubleshootingKB(
        machine_id=payload.machine_id, symptom=payload.symptom, guidance=payload.guidance,
        wire_refs=payload.wire_refs, source="tech", verified=True,
        embedding=_embed_sync(f"{payload.symptom}\n{payload.guidance}"),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/kb/{entry_id}", status_code=204)
def delete_kb(entry_id: int, db: Session = Depends(get_db)):
    e = db.get(TroubleshootingKB, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="KB entry not found")
    db.delete(e)
    db.commit()
