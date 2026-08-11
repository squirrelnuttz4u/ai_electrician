"""Machine CRUD — machines are the organizing unit; prints belong to machines."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models import Machine, Print
from ..schemas import MachineCreate, MachineOut, MachineUpdate

router = APIRouter(prefix="/api/machines", tags=["machines"], dependencies=[Depends(require_auth)])


def _to_out(m: Machine, print_count: int) -> MachineOut:
    return MachineOut(
        id=m.id, name=m.name, description=m.description, location=m.location,
        tags=m.tags, created_at=m.created_at, print_count=print_count,
    )


@router.get("", response_model=list[MachineOut])
def list_machines(db: Session = Depends(get_db)):
    counts = dict(
        db.query(Print.machine_id, func.count(Print.id)).group_by(Print.machine_id).all()
    )
    machines = db.query(Machine).order_by(Machine.name).all()
    return [_to_out(m, counts.get(m.id, 0)) for m in machines]


@router.post("", response_model=MachineOut, status_code=201)
def create_machine(payload: MachineCreate, db: Session = Depends(get_db)):
    if db.query(Machine).filter(Machine.name == payload.name).first():
        raise HTTPException(status_code=409, detail="A machine with that name already exists")
    m = Machine(**payload.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _to_out(m, 0)


@router.get("/{machine_id}", response_model=MachineOut)
def get_machine(machine_id: int, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")
    count = db.query(func.count(Print.id)).filter(Print.machine_id == machine_id).scalar() or 0
    return _to_out(m, count)


@router.patch("/{machine_id}", response_model=MachineOut)
def update_machine(machine_id: int, payload: MachineUpdate, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    count = db.query(func.count(Print.id)).filter(Print.machine_id == machine_id).scalar() or 0
    return _to_out(m, count)


@router.delete("/{machine_id}", status_code=204)
def delete_machine(machine_id: int, db: Session = Depends(get_db)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")
    db.delete(m)  # cascades to prints, pages, components, wires, chunks
    db.commit()
