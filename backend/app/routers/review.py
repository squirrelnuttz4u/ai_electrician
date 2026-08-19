"""Verify & correct loop.

Techs correct extracted components/wires (which immediately improves the facts
the AI reasons over) and manage the per-machine troubleshooting knowledge base.
Every correction is recorded in `corrections` for auditability and future use.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..extraction import component_summary, wire_summary
from ..models import Component, Correction, DocChunk, Print, PrintPage, TroubleshootingKB, Wire
from ..ollama_client import embed
from ..schemas import (
    AccuracyOut,
    ComponentCreate,
    ComponentOut,
    ComponentUpdate,
    PageReviewIn,
    PageReviewOut,
    WireCreate,
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
    # Record the rejection before deleting. A hallucination that simply vanishes
    # leaves no evidence, and precision cannot be computed from survivors alone.
    if c.origin == "extracted":
        db.add(Correction(target_type="component", target_id=c.id, print_id=c.print_id,
                          machine_id=_machine_of(db, c.print_id),
                          original={"designator": c.designator, "type": c.type,
                                    "voltage": c.voltage, "description": c.description},
                          corrected=None, note="rejected"))
    db.delete(c)
    db.commit()


@router.delete("/wires/{wire_id}", status_code=204)
def delete_wire(wire_id: int, db: Session = Depends(get_db)):
    w = db.get(Wire, wire_id)
    if not w:
        raise HTTPException(status_code=404, detail="Wire not found")
    if w.origin == "extracted":
        db.add(Correction(target_type="wire", target_id=w.id, print_id=w.print_id,
                          machine_id=_machine_of(db, w.print_id),
                          original={"wire_number": w.wire_number, "voltage": w.voltage,
                                    "from_ref": w.from_ref, "to_ref": w.to_ref},
                          corrected=None, note="rejected"))
    db.delete(w)
    db.commit()


# ---------- adding what the model missed (required to measure recall) ----------
@router.post("/prints/{print_id}/components", response_model=ComponentOut, status_code=201)
def add_component(print_id: int, payload: ComponentCreate, db: Session = Depends(get_db)):
    """Add a component the extractor missed. Recorded as origin='manual' so it
    counts against recall rather than being mistaken for a successful read."""
    c = Component(print_id=print_id, page_number=payload.page_number,
                  designator=payload.designator, type=payload.type,
                  description=payload.description, voltage=payload.voltage,
                  confidence=1.0, verified=True, origin="manual")
    db.add(c)
    db.flush()
    _refresh_chunk(db, print_id, c.page_number, "component", c.id,
                   component_summary(c.designator, c.type, c.voltage, c.description))
    db.commit()
    db.refresh(c)
    return c


@router.post("/prints/{print_id}/wires", response_model=WireOut, status_code=201)
def add_wire(print_id: int, payload: WireCreate, db: Session = Depends(get_db)):
    """Add a wire the extractor missed. See add_component."""
    w = Wire(print_id=print_id, page_number=payload.page_number,
             wire_number=payload.wire_number, voltage=payload.voltage,
             color=payload.color, from_ref=payload.from_ref, to_ref=payload.to_ref,
             confidence=1.0, verified=True, origin="manual")
    db.add(w)
    db.flush()
    _refresh_chunk(db, print_id, w.page_number, "wire", w.id,
                   wire_summary(w.wire_number, w.voltage, w.from_ref, w.to_ref))
    db.commit()
    db.refresh(w)
    return w


# ---------- golden set ----------
@router.post("/prints/{print_id}/pages/{page_number}/reviewed", response_model=PageReviewOut)
def set_page_reviewed(print_id: int, page_number: int, payload: PageReviewIn,
                      db: Session = Depends(get_db)):
    """Mark a page as fully checked. Only reviewed pages count as ground truth."""
    page = (db.query(PrintPage)
              .filter(PrintPage.print_id == print_id, PrintPage.page_number == page_number)
              .first())
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    page.reviewed = payload.reviewed
    db.commit()
    db.refresh(page)
    return PageReviewOut(print_id=print_id, page_number=page_number, reviewed=page.reviewed)


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


@router.get("/machines/{machine_id}/accuracy", response_model=AccuracyOut)
def machine_accuracy(machine_id: int, db: Session = Depends(get_db)):
    """Extraction accuracy over pages a technician has marked fully reviewed.

    Only reviewed pages count. On an unreviewed page an absent entity is
    ambiguous — the model may have missed it, or nobody has looked yet — so
    including them would silently inflate every figure.

        precision = kept / (kept + rejected)      how much of what it read was real
        recall    = kept / (kept + missed)        how much of what was there it found
        correction rate = corrected / kept        how much of the rest needed fixing

    Anything a tech had to type in themselves (origin='manual') is a miss.
    """
    reviewed_pages = db.execute(sql_text(
        """
        SELECT pp.print_id, pp.page_number
        FROM print_pages pp JOIN prints p ON pp.print_id = p.id
        WHERE p.machine_id = :mid AND pp.reviewed
        """), {"mid": machine_id}).all()

    if not reviewed_pages:
        return AccuracyOut(machine_id=machine_id, reviewed_pages=0, kept=0, rejected=0,
                           missed=0, corrected=0, precision=None, recall=None,
                           correction_rate=None,
                           note="No pages marked reviewed yet — mark a page reviewed to start measuring.")

    keys = [(pid, pno) for pid, pno in reviewed_pages]
    pairs = ", ".join(f"({p},{n})" for p, n in keys)

    def scalar(sql: str) -> int:
        return int(db.execute(sql_text(sql), {"mid": machine_id}).scalar() or 0)

    kept = scalar(f"""
        SELECT count(*) FROM (
            SELECT print_id, page_number FROM components
             WHERE origin = 'extracted' AND (print_id, page_number) IN ({pairs})
            UNION ALL
            SELECT print_id, page_number FROM wires
             WHERE origin = 'extracted' AND (print_id, page_number) IN ({pairs})
        ) t""")

    missed = scalar(f"""
        SELECT count(*) FROM (
            SELECT print_id, page_number FROM components
             WHERE origin = 'manual' AND (print_id, page_number) IN ({pairs})
            UNION ALL
            SELECT print_id, page_number FROM wires
             WHERE origin = 'manual' AND (print_id, page_number) IN ({pairs})
        ) t""")

    # Rejections and edits are page-agnostic in `corrections` (the row is gone),
    # so they are scoped by print and machine.
    print_ids = ", ".join(str(p) for p in {p for p, _ in keys})
    # Discriminate on note, not on `corrected IS NULL`. JSONB stores Python None
    # as JSON 'null', which is not SQL NULL, so the null test silently counted
    # every rejection as an edit and reported precision 1.00 after a deletion.
    rejected = scalar(f"""
        SELECT count(*) FROM corrections
         WHERE machine_id = :mid AND note = 'rejected'
           AND target_type IN ('component','wire') AND print_id IN ({print_ids})""")
    corrected = scalar(f"""
        SELECT count(DISTINCT target_id) FROM corrections
         WHERE machine_id = :mid AND note IS DISTINCT FROM 'rejected'
           AND target_type IN ('component','wire') AND print_id IN ({print_ids})""")

    read_total = kept + rejected
    truth_total = kept + missed
    return AccuracyOut(
        machine_id=machine_id,
        reviewed_pages=len(keys),
        kept=kept, rejected=rejected, missed=missed, corrected=corrected,
        precision=round(kept / read_total, 3) if read_total else None,
        recall=round(kept / truth_total, 3) if truth_total else None,
        correction_rate=round(corrected / kept, 3) if kept else None,
        note=None,
    )
