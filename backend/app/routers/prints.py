"""Print upload / list / delete, plus serving page images and the original PDF."""
from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import settings
from ..database import get_db
from ..models import Component, Machine, Print, PrintPage, Wire
from ..pdf_processing import sha256_file
from ..queue import enqueue_ingest
from ..schemas import ComponentOut, PageOut, PrintOut, WireOut

router = APIRouter(prefix="/api", tags=["prints"], dependencies=[Depends(require_auth)])


@router.get("/machines/{machine_id}/prints", response_model=list[PrintOut])
def list_prints(machine_id: int, db: Session = Depends(get_db)):
    if not db.get(Machine, machine_id):
        raise HTTPException(status_code=404, detail="Machine not found")
    return (
        db.query(Print)
        .filter(Print.machine_id == machine_id)
        .order_by(Print.uploaded_at.desc())
        .all()
    )


@router.post("/machines/{machine_id}/prints", response_model=PrintOut, status_code=201)
async def upload_print(
    machine_id: int,
    title: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Machine, machine_id):
        raise HTTPException(status_code=404, detail="Machine not found")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    prints_dir = os.path.join(settings.data_dir, "prints", str(machine_id))
    os.makedirs(prints_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.pdf"
    filepath = os.path.join(prints_dir, stored_name)
    with open(filepath, "wb") as out:
        shutil.copyfileobj(file.file, out)

    digest = sha256_file(filepath)
    existing = (
        db.query(Print)
        .filter(Print.machine_id == machine_id, Print.sha256 == digest)
        .first()
    )
    if existing:
        os.remove(filepath)
        raise HTTPException(status_code=409, detail="This exact PDF is already uploaded for this machine")

    pr = Print(
        machine_id=machine_id,
        title=title,
        filename=file.filename or stored_name,
        filepath=filepath,
        sha256=digest,
        status="processing",
        status_detail="Queued for processing",
    )
    db.add(pr)
    db.commit()
    db.refresh(pr)

    await enqueue_ingest(pr.id)
    return pr


@router.get("/prints/{print_id}", response_model=PrintOut)
def get_print(print_id: int, db: Session = Depends(get_db)):
    pr = db.get(Print, print_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Print not found")
    return pr


@router.get("/prints/{print_id}/pages", response_model=list[PageOut])
def list_pages(print_id: int, db: Session = Depends(get_db)):
    if not db.get(Print, print_id):
        raise HTTPException(status_code=404, detail="Print not found")
    return (
        db.query(PrintPage)
        .filter(PrintPage.print_id == print_id)
        .order_by(PrintPage.page_number)
        .all()
    )


@router.get("/prints/{print_id}/components", response_model=list[ComponentOut])
def list_components(print_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Component)
        .filter(Component.print_id == print_id)
        .order_by(Component.page_number, Component.designator)
        .all()
    )


@router.get("/prints/{print_id}/wires", response_model=list[WireOut])
def list_wires(print_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Wire)
        .filter(Wire.print_id == print_id)
        .order_by(Wire.page_number, Wire.wire_number)
        .all()
    )


@router.get("/prints/{print_id}/file")
def get_pdf(print_id: int, db: Session = Depends(get_db)):
    pr = db.get(Print, print_id)
    if not pr or not os.path.exists(pr.filepath):
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(pr.filepath, media_type="application/pdf", filename=pr.filename)


@router.get("/prints/{print_id}/pages/{page_number}/image")
def get_page_image(print_id: int, page_number: int, db: Session = Depends(get_db)):
    page = (
        db.query(PrintPage)
        .filter(PrintPage.print_id == print_id, PrintPage.page_number == page_number)
        .first()
    )
    if not page or not page.image_path or not os.path.exists(page.image_path):
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(page.image_path, media_type="image/png")


@router.delete("/prints/{print_id}", status_code=204)
def delete_print(print_id: int, db: Session = Depends(get_db)):
    pr = db.get(Print, print_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Print not found")

    # remove files: the PDF and the rendered page images
    try:
        if pr.filepath and os.path.exists(pr.filepath):
            os.remove(pr.filepath)
        pages_dir = os.path.join(settings.data_dir, "pages", str(print_id))
        if os.path.isdir(pages_dir):
            shutil.rmtree(pages_dir, ignore_errors=True)
    except OSError:
        pass

    db.delete(pr)  # cascades to pages, components, wires, chunks, connections
    db.commit()
