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

LOW_CONFIDENCE = settings.review_confidence_threshold


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

        # What this machine's other prints have already taught us. Built once per
        # print: it cannot change mid-ingest, and it costs two queries.
        vocabulary = _machine_vocabulary(db, pr.machine_id, exclude_print_id=print_id)

        low_conf_count = 0
        total_entities = 0
        failed_pages: list[int] = []   # vision call errored — NOT the same as an empty page
        barren_pages: list[int] = []   # real text present, but nothing extracted

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
            multi = await extraction.extract_page_multi(image_bytes, page.text, vocabulary=vocabulary)
            result = {
                "components": multi.components,
                "wires": multi.wires,
                "connections": multi.connections,
                "error": multi.error,
            }

            # A failed vision call returns empty lists, which is indistinguishable
            # from a legitimately empty page (a legend or notes sheet) unless we
            # record it here. Left unrecorded, a timed-out page silently reports
            # "no components on this sheet".
            if result.get("error"):
                failed_pages.append(page.page_number)
            elif not (result["components"] or result["wires"]):
                # Legends and notes sheets genuinely have no devices; a dense
                # schematic that yields nothing is a different matter, so only
                # flag pages that carried a meaningful amount of text.
                if len(page.text.strip()) >= 200:
                    barren_pages.append(page.page_number)

            for comp in result["components"]:
                designator = str(comp.get("designator", "")).strip()
                if not designator:
                    continue
                conf = extraction.score_confidence(
                    comp.get("confidence"),
                    extraction.corroborated(designator, page.text),
                    agreement=comp.get("agreement", 1.0),
                )
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
                conf = extraction.score_confidence(
                    wire.get("confidence"),
                    extraction.corroborated(wnum, page.text),
                    agreement=wire.get("agreement", 1.0),
                )
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

        # Finalize status. Anything the tech should look at wins over "ready":
        # a silent failure that reports success is worse than an honest flag.
        problems: list[str] = []
        if failed_pages:
            problems.append(f"vision extraction failed on page(s) {_ranges(failed_pages)}")
        if barren_pages:
            problems.append(f"no components/wires found on page(s) {_ranges(barren_pages)}")
        if low_conf_count:
            problems.append(f"{low_conf_count} low-confidence item(s)")

        if total_entities == 0:
            pr.status = "review"
            pr.status_detail = "No components/wires were extracted — review recommended."
        elif problems:
            pr.status = "review"
            pr.status_detail = f"Extracted {total_entities} item(s); " + "; ".join(problems) + "."
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


def _machine_vocabulary(db: Session, machine_id: int, exclude_print_id: int) -> str:
    """Confirmed labels and past misreads for a machine, as a prompt block.

    Only tech-verified entities count: feeding unverified extractions back in
    would let one print's hallucination seed the next print's prompt.
    """
    from sqlalchemy import text as sql_text

    try:
        designators = [
            r[0] for r in db.execute(
                sql_text(
                    """
                    SELECT DISTINCT c.designator
                    FROM components c JOIN prints p ON c.print_id = p.id
                    WHERE p.machine_id = :mid AND c.verified AND c.print_id <> :pid
                    ORDER BY c.designator LIMIT 200
                    """
                ),
                {"mid": machine_id, "pid": exclude_print_id},
            ).all()
        ]
        wires = [
            r[0] for r in db.execute(
                sql_text(
                    """
                    SELECT DISTINCT w.wire_number
                    FROM wires w JOIN prints p ON w.print_id = p.id
                    WHERE p.machine_id = :mid AND w.verified AND w.print_id <> :pid
                    ORDER BY w.wire_number LIMIT 200
                    """
                ),
                {"mid": machine_id, "pid": exclude_print_id},
            ).all()
        ]
        # Misreads worth warning about are the ones that happened more than once.
        pairs = [
            (r[0], r[1]) for r in db.execute(
                sql_text(
                    """
                    SELECT original->>'designator' AS was,
                           corrected->>'designator' AS now,
                           count(*) AS n
                    FROM corrections
                    WHERE machine_id = :mid AND target_type = 'component'
                      AND original->>'designator' IS DISTINCT FROM corrected->>'designator'
                    GROUP BY 1, 2 HAVING count(*) > 1
                    ORDER BY n DESC LIMIT 15
                    """
                ),
                {"mid": machine_id},
            ).all()
        ]
    except Exception:  # noqa: BLE001 - a hint is an optimisation, never fatal
        return ""

    return extraction.build_vocabulary_hint(designators, wires, pairs)


def _ranges(pages: list[int]) -> str:
    """Condense a page list into a compact human-readable string: 3, 11-13, 15."""
    if not pages:
        return ""
    ordered = sorted(set(pages))
    spans: list[str] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(spans)


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
