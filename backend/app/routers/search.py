"""Full-text / keyword search across a machine's prints — used by the viewer to
confirm what the AI reports (search a wire number, jump to the page)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models import Component, Print, PrintPage, Wire
from ..schemas import SearchHit

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(..., min_length=1),
    machine_id: int | None = None,
    db: Session = Depends(get_db),
):
    like = f"%{q}%"
    titles = dict(db.query(Print.id, Print.title).all())
    machine_of = dict(db.query(Print.id, Print.machine_id).all())

    def in_scope(pid: int) -> bool:
        return machine_id is None or machine_of.get(pid) == machine_id

    hits: list[SearchHit] = []

    # wires — exact/partial on wire number is the most common confirmation query
    wq = db.query(Wire).filter(or_(Wire.wire_number.ilike(like), Wire.voltage.ilike(like)))
    for w in wq.limit(50):
        if not in_scope(w.print_id):
            continue
        hits.append(SearchHit(
            print_id=w.print_id, print_title=titles.get(w.print_id, ""),
            machine_id=machine_of.get(w.print_id, 0), page_number=w.page_number,
            kind="wire", score=1.0,
            snippet=f"Wire {w.wire_number}" + (f" · {w.voltage}" if w.voltage else "")
                    + (f" · {w.from_ref}→{w.to_ref}" if (w.from_ref or w.to_ref) else ""),
        ))

    # components
    cq = db.query(Component).filter(
        or_(Component.designator.ilike(like), Component.type.ilike(like), Component.description.ilike(like))
    )
    for c in cq.limit(50):
        if not in_scope(c.print_id):
            continue
        hits.append(SearchHit(
            print_id=c.print_id, print_title=titles.get(c.print_id, ""),
            machine_id=machine_of.get(c.print_id, 0), page_number=c.page_number,
            kind="component", score=0.9,
            snippet=f"{c.designator}" + (f" · {c.type}" if c.type else "")
                    + (f" · {c.description}" if c.description else ""),
        ))

    # page text
    pq = db.query(PrintPage).filter(PrintPage.extracted_text.ilike(like))
    for p in pq.limit(50):
        if not in_scope(p.print_id):
            continue
        text = p.extracted_text or ""
        idx = text.lower().find(q.lower())
        start = max(0, idx - 60)
        snippet = ("…" if start > 0 else "") + text[start:start + 160].replace("\n", " ").strip() + "…"
        hits.append(SearchHit(
            print_id=p.print_id, print_title=titles.get(p.print_id, ""),
            machine_id=machine_of.get(p.print_id, 0), page_number=p.page_number,
            kind="text", score=0.6, snippet=snippet,
        ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:60]
