"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Machines ----------
class MachineCreate(BaseModel):
    name: str
    description: str | None = None
    location: str | None = None
    tags: list[str] | None = None


class MachineUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    location: str | None = None
    tags: list[str] | None = None


class MachineOut(ORMModel):
    id: int
    name: str
    description: str | None
    location: str | None
    tags: list[str] | None
    created_at: datetime
    print_count: int = 0


# ---------- Prints ----------
class PrintOut(ORMModel):
    id: int
    machine_id: int
    title: str
    filename: str
    sha256: str
    page_count: int
    status: str
    status_detail: str | None
    uploaded_at: datetime


class PageOut(ORMModel):
    id: int
    print_id: int
    page_number: int
    has_text_layer: bool
    used_ocr: bool
    width: int | None
    height: int | None


# ---------- Components / Wires ----------
class ComponentOut(ORMModel):
    id: int
    print_id: int
    page_number: int
    designator: str
    type: str | None
    description: str | None
    voltage: str | None
    bbox: list | None
    confidence: float
    verified: bool


class ComponentUpdate(BaseModel):
    designator: str | None = None
    type: str | None = None
    description: str | None = None
    voltage: str | None = None
    bbox: list | None = None
    verified: bool | None = None
    note: str | None = None


class WireOut(ORMModel):
    id: int
    print_id: int
    page_number: int
    wire_number: str
    voltage: str | None
    color: str | None
    from_ref: str | None
    to_ref: str | None
    bbox: list | None
    confidence: float
    verified: bool


class WireUpdate(BaseModel):
    wire_number: str | None = None
    voltage: str | None = None
    color: str | None = None
    from_ref: str | None = None
    to_ref: str | None = None
    bbox: list | None = None
    verified: bool | None = None
    note: str | None = None


# ---------- Search ----------
class SearchHit(BaseModel):
    print_id: int
    print_title: str
    machine_id: int
    page_number: int | None
    kind: str
    snippet: str
    score: float


# ---------- Chat ----------
class ChatAsk(BaseModel):
    machine_id: int
    message: str
    session_id: int | None = None


class Citation(BaseModel):
    print_id: int
    print_title: str | None = None
    page_number: int | None = None
    kind: str
    ref: str | None = None
    snippet: str | None = None


class ChatMessageOut(ORMModel):
    id: int
    session_id: int
    role: str
    content: str
    citations: list | None
    feedback: str | None
    created_at: datetime


class ChatSessionOut(ORMModel):
    id: int
    machine_id: int
    title: str | None
    created_at: datetime


class FeedbackIn(BaseModel):
    feedback: str  # good | bad
    corrected_guidance: str | None = None


# ---------- KB ----------
class KBEntryIn(BaseModel):
    machine_id: int
    symptom: str
    guidance: str
    wire_refs: list[str] | None = None


class KBEntryOut(ORMModel):
    id: int
    machine_id: int
    symptom: str
    guidance: str
    wire_refs: list | None
    source: str | None
    verified: bool
    created_at: datetime
