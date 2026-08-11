"""End-to-end ingestion of an uploaded print.

Called by the background worker. Steps:
  1. render pages + extract/OCR text (pdf_processing)
  2. vision extraction of components/wires/connections (extraction)
  3. persist entities (unverified, with confidence)
  4. build doc_chunks (page text + entity summaries) and embed them (rag/ollama)
  5. set the print status to `ready` or `review`

Kept import-light and synchronous-DB so it runs cleanly inside the ARQ worker.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy.orm import Session

from . import extraction, ollama_client, pdf_processing
from .config import settings
from .database import SessionLocal
from .models import Component, Connection, DocChunk, Print, PrintPage, Wire

LOW_CONFIDENCE = 0.55


async def _embed_safe(text_value: str) -> list[float] | None:
    try:
        return await ollama_client.embed(text_value)
    except Exception:  # noqa: BLE001 - embeddings optional; keyword search still works
        return None


async def ingest_print(print_id: int) -> None:
    db: Session = SessionLocal()
    try:
        pr = db.get(Print, print_id)
        if pr is None:
            return
        pr.status = "processing"
        pr.status_detail = "Rendering pages and extracting text"
        db.commit()

        out_dir = os.path.join(settings.data_dir, "pages", str(print_id))
        pages = pdf_processing.process_pdf(pr.filepath, out_dir)
        pr.page_count = len(pages)
        db.commit()

        low_conf_count = 0
        total_entities = 0

        for page in pages:
            db.add(
                PrintPage(
                    print_id=print_id,
                    page_number=page.page_number,
                    image_path=page.image_path,
                    has_text_layer=page.has_text_layer,
                    used_ocr=page.used_ocr,
                    extracted_text=page.text,
                    width=page.width,
                    height=page.height,
                )
            )
            db.commit()

            # page-text chunk
            if page.text.strip():
                emb = await _embed_safe(page.text[:4000])
                db.add(
                    DocChunk(
                        print_id=print_id,
                        machine_id=pr.machine_id,
                        page_number=page.page_number,
                        kind="page_text",
                        content=page.text[:8000],
                        embedding=emb,
                    )
                )
                db.commit()

            # vision extraction
            pr.status_detail = f"Reading schematic page {page.page_number}/{len(pages)}"
            db.commit()
            image_bytes = pdf_processing.load_page_image(page.image_path)
            result = await extraction.extract_page(image_bytes, page.text)

            for comp in result["components"]:
                designator = str(comp.get("designator", "")).strip()
                if not designator:
                    continue
                conf = _float(comp.get("confidence"))
                comp_row = Component(
                    print_id=print_id,
                    page_number=page.page_number,
                    designator=designator,
                    type=_str(comp.get("type")),
                    description=_str(comp.get("description")),
                    voltage=_str(comp.get("voltage")),
                    confidence=conf,
                    verified=False,
                )
                db.add(comp_row)
                db.flush()  # assign comp_row.id for the chunk link
                summary = extraction.component_summary(
                    designator, _str(comp.get("type")), _str(comp.get("voltage")), _str(comp.get("description"))
                )
                db.add(
                    DocChunk(
                        print_id=print_id,
                        machine_id=pr.machine_id,
                        page_number=page.page_number,
                        kind="component",
                        source_id=comp_row.id,
                        content=summary,
                        embedding=await _embed_safe(summary),
                    )
                )
                total_entities += 1
                if conf < LOW_CONFIDENCE:
                    low_conf_count += 1

            for wire in result["wires"]:
                wnum = str(wire.get("wire_number", "")).strip()
                if not wnum:
                    continue
                conf = _float(wire.get("confidence"))
                wire_row = Wire(
                    print_id=print_id,
                    page_number=page.page_number,
                    wire_number=wnum,
                    voltage=_str(wire.get("voltage")),
                    color=_str(wire.get("color")),
                    from_ref=_str(wire.get("from_ref")),
                    to_ref=_str(wire.get("to_ref")),
                    confidence=conf,
                    verified=False,
                )
                db.add(wire_row)
                db.flush()  # assign wire_row.id for the chunk link
                summary = extraction.wire_summary(
                    wnum, _str(wire.get("voltage")), _str(wire.get("from_ref")), _str(wire.get("to_ref"))
                )
                db.add(
                    DocChunk(
                        print_id=print_id,
                        machine_id=pr.machine_id,
                        page_number=page.page_number,
                        kind="wire",
                        source_id=wire_row.id,
                        content=summary,
                        embedding=await _embed_safe(summary),
                    )
                )
                total_entities += 1
                if conf < LOW_CONFIDENCE:
                    low_conf_count += 1

            for conn in result["connections"]:
                wnum = str(conn.get("wire_number", "")).strip()
                desig = str(conn.get("component_designator", "")).strip()
                if not (wnum and desig):
                    continue
                db.add(
                    Connection(
                        print_id=print_id,
                        wire_number=wnum,
                        component_designator=desig,
                        terminal=_str(conn.get("terminal")),
                        confidence=_float(conn.get("confidence")),
                    )
                )
            db.commit()

        # finalize status
        if total_entities == 0:
            pr.status = "review"
            pr.status_detail = "No components/wires were extracted — review recommended."
        elif low_conf_count > 0:
            pr.status = "review"
            pr.status_detail = f"{low_conf_count} low-confidence item(s) flagged for review."
        else:
            pr.status = "ready"
            pr.status_detail = f"Extracted {total_entities} item(s)."
        db.commit()
    except Exception as exc:  # noqa: BLE001 - record failure on the print row
        db.rollback()
        pr = db.get(Print, print_id)
        if pr is not None:
            pr.status = "error"
            pr.status_detail = f"Ingestion failed: {exc}"[:1000]
            db.commit()
    finally:
        db.close()


def _str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _float(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def ingest_print_sync(print_id: int) -> None:
    """Synchronous entry point (used as a fallback if no worker is running)."""
    asyncio.run(ingest_print(print_id))
