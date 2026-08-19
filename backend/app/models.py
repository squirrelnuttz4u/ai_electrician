"""SQLAlchemy ORM models — the full data model for AI Electrician.

Entity map
----------
machine            an industrial machine; prints are organized under it
print              an uploaded PDF schematic belonging to a machine
print_page         one rendered page of a print (image + extracted text)
component          a device on a print (contactor M3, motor, breaker, ...)
wire               a wire/conductor on a print (wire number A1, voltage, ...)
connection         netlist edge linking a component terminal to a wire
doc_chunk          a text chunk with an embedding, for hybrid RAG search
correction         a human correction to an extraction or an AI answer
troubleshooting_kb verified symptom -> guidance, grows from confirmed answers
chat_session       a troubleshooting conversation, scoped to a machine
chat_message       one message in a chat session, with citations
"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import settings
from .database import Base


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    location: Mapped[str | None] = mapped_column(String(200), default=None)
    tags: Mapped[list | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prints: Mapped[list["Print"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan"
    )


class Print(Base):
    __tablename__ = "prints"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300))
    filepath: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # processing | ready | review | error
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    machine: Mapped["Machine"] = relationship(back_populates="prints")
    pages: Mapped[list["PrintPage"]] = relationship(
        back_populates="print", cascade="all, delete-orphan"
    )
    components: Mapped[list["Component"]] = relationship(
        back_populates="print", cascade="all, delete-orphan"
    )
    wires: Mapped[list["Wire"]] = relationship(
        back_populates="print", cascade="all, delete-orphan"
    )


class PrintPage(Base):
    __tablename__ = "print_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based
    image_path: Mapped[str | None] = mapped_column(String(500), default=None)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    # A tech asserting "this page is now fully correct". Reviewed pages are the
    # ground truth that extraction accuracy is measured against.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    print: Mapped["Print"] = relationship(back_populates="pages")


class Component(Base):
    __tablename__ = "components"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    designator: Mapped[str] = mapped_column(String(60), index=True)  # e.g. M3, CR1, CB2
    type: Mapped[str | None] = mapped_column(String(80), default=None)  # contactor, motor...
    description: Mapped[str | None] = mapped_column(Text, default=None)
    voltage: Mapped[str | None] = mapped_column(String(40), default=None)
    # bbox as [x0, y0, x1, y1] in image pixels
    bbox: Mapped[list | None] = mapped_column(JSONB, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # extracted = the model produced it; manual = a tech added what the model
    # missed. Needed to measure recall.
    origin: Mapped[str] = mapped_column(String(20), default="extracted", index=True)

    print: Mapped["Print"] = relationship(back_populates="components")


class Wire(Base):
    __tablename__ = "wires"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    wire_number: Mapped[str] = mapped_column(String(60), index=True)  # e.g. A1, 120, X2
    voltage: Mapped[str | None] = mapped_column(String(40), default=None)
    color: Mapped[str | None] = mapped_column(String(40), default=None)
    from_ref: Mapped[str | None] = mapped_column(String(120), default=None)
    to_ref: Mapped[str | None] = mapped_column(String(120), default=None)
    bbox: Mapped[list | None] = mapped_column(JSONB, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # extracted = the model produced it; manual = a tech added what the model
    # missed. Needed to measure recall.
    origin: Mapped[str] = mapped_column(String(20), default="extracted", index=True)

    print: Mapped["Print"] = relationship(back_populates="wires")


class Connection(Base):
    """Netlist edge: a wire connects to a component terminal."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), index=True
    )
    wire_number: Mapped[str] = mapped_column(String(60), index=True)
    component_designator: Mapped[str] = mapped_column(String(60), index=True)
    terminal: Mapped[str | None] = mapped_column(String(60), default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class DocChunk(Base):
    """A retrievable chunk of knowledge with a vector embedding.

    Sources: page text, structured component/wire summaries. Full-text search
    uses the `content` column via a Postgres tsvector GIN index (created in the
    migration); semantic search uses the `embedding` column via pgvector.
    """

    __tablename__ = "doc_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), index=True
    )
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, default=None)
    kind: Mapped[str] = mapped_column(String(30), default="page_text")  # page_text|component|wire
    # id of the source Component/Wire (when kind is component/wire) so a
    # correction can update this exact chunk instead of leaving a stale copy.
    source_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(settings.embed_dim), default=None)


class Correction(Base):
    """A human correction — to an extracted entity or to an AI answer."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    # component | wire | connection | answer
    target_type: Mapped[str] = mapped_column(String(30), index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, default=None)
    print_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    machine_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    original: Mapped[dict | None] = mapped_column(JSONB, default=None)
    corrected: Mapped[dict | None] = mapped_column(JSONB, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TroubleshootingKB(Base):
    """Verified symptom -> guidance entries. Grows from confirmed answers and
    corrections; retrieved as authoritative context on future questions."""

    __tablename__ = "troubleshooting_kb"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    symptom: Mapped[str] = mapped_column(Text)
    guidance: Mapped[str] = mapped_column(Text)
    wire_refs: Mapped[list | None] = mapped_column(JSONB, default=None)
    source: Mapped[str | None] = mapped_column(String(40), default="tech")  # tech|confirmed_answer
    verified: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    embedding: Mapped[list | None] = mapped_column(Vector(settings.embed_dim), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # citations: list of {print_id, page_number, kind, ref, snippet}
    citations: Mapped[list | None] = mapped_column(JSONB, default=None)
    # feedback: null | "good" | "bad"
    feedback: Mapped[str | None] = mapped_column(String(10), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
