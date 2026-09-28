"""
Restaurant demo backend for a Retell AI voice agent.
Persists reservations and orders in SQLite (swap DATABASE_URL for Postgres in production).

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000

Deploy: push to Render/Railway. For SQLite to survive restarts on Render, attach a
persistent disk mounted at /data and set DATABASE_URL=sqlite:////data/restaurant.db
(otherwise the filesystem resets on every deploy). For anything beyond a demo,
switch to a managed Postgres instance instead.
"""

import os
import uuid
from datetime import datetime, date as date_type
from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Enum as SAEnum
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from fastapi import Depends

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./restaurant.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Config: dining-room capacity model
# ---------------------------------------------------------------------------

# Simple capacity model for the demo: total covers (seats) available per
# 30-minute slot. Real system would model individual tables; this is enough
# to make availability checks behave sensibly against concurrent bookings.
TOTAL_COVERS_PER_SLOT = 40
VALID_SLOT_MINUTES = {0, 30}


class ReservationStatus(str, Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"


class OrderStatus(str, Enum):
    received = "received"
    preparing = "preparing"
    ready = "ready"
    completed = "completed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(String, primary_key=True, default=lambda: f"RES-{uuid.uuid4().hex[:8].upper()}")
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False, index=True)
    party_size = Column(Integer, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    time = Column(String, nullable=False)  # HH:MM (24hr)
    status = Column(SAEnum(ReservationStatus), default=ReservationStatus.confirmed)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True, default=lambda: f"ORD-{uuid.uuid4().hex[:8].upper()}")
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False, index=True)
    items = Column(String, nullable=False)  # comma-separated for simplicity
    fulfillment = Column(String, nullable=False)  # "pickup" | "delivery"
    status = Column(SAEnum(OrderStatus), default=OrderStatus.received)
    eta_minutes = Column(Integer, default=25)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# Pydantic schemas (request/response shapes)
# ---------------------------------------------------------------------------

class ReservationCreate(BaseModel):
    name: str
    phone: str
    party_size: int = Field(gt=0, le=20)
    date: str  # YYYY-MM-DD
    time: str  # HH:MM


class OrderCreate(BaseModel):
    name: str
    phone: str
    items: list[str]
    fulfillment: str  # "pickup" | "delivery"


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Restaurant Voice Agent Backend")


def _validate_time(time_str: str):
    try:
        hh, mm = time_str.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and mm in VALID_SLOT_MINUTES):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="time must be HH:MM on a 30-minute slot, e.g. 18:30")


def _validate_date(date_str: str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


def _covers_booked(db: Session, date: str, time: str) -> int:
    rows = (
        db.query(Reservation)
        .filter(
            Reservation.date == date,
            Reservation.time == time,
            Reservation.status == ReservationStatus.confirmed,
        )
        .all()
    )
    return sum(r.party_size for r in rows)


def _nearby_slots(time_str: str) -> list[str]:
    hh, mm = map(int, time_str.split(":"))
    total = hh * 60 + mm
    offsets = [-60, -30, 30, 60]
    slots = []
    for off in offsets:
        t = total + off
        if 0 <= t < 24 * 60:
            slots.append(f"{t // 60:02d}:{t % 60:02d}")
    return slots


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@app.get("/availability")
def check_availability(
    date: str = Query(...),
    time: str = Query(...),
    party_size: int = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    _validate_date(date)
    _validate_time(time)

    booked = _covers_booked(db, date, time)
    remaining = TOTAL_COVERS_PER_SLOT - booked

    if remaining >= party_size:
        return {"available": True, "remaining_covers": remaining, "alternative_times": []}

    # Suggest nearby slots that could fit the party
    alternatives = []
    for slot in _nearby_slots(time):
        if TOTAL_COVERS_PER_SLOT - _covers_booked(db, date, slot) >= party_size:
            alternatives.append(slot)

    return {"available": False, "remaining_covers": max(remaining, 0), "alternative_times": alternatives}


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------

@app.post("/reservations")
def create_reservation(res: ReservationCreate, db: Session = Depends(get_db)):
    _validate_date(res.date)
    _validate_time(res.time)

    booked = _covers_booked(db, res.date, res.time)
    if TOTAL_COVERS_PER_SLOT - booked < res.party_size:
        raise HTTPException(status_code=409, detail="No availability for that date/time/party size")

    reservation = Reservation(**res.dict())
    db.add(reservation)
    db.commit()
    db.refresh(reservation)

    return {
        "status": "confirmed",
        "confirmation_id": reservation.id,
        "name": reservation.name,
        "date": reservation.date,
        "time": reservation.time,
        "party_size": reservation.party_size,
    }


@app.get("/reservations")
def list_reservations(
    date: Optional[str] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Reservation)
    if date:
        q = q.filter(Reservation.date == date)
    if phone:
        q = q.filter(Reservation.phone == phone)
    return q.order_by(Reservation.date, Reservation.time).all()


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str, db: Session = Depends(get_db)):
    res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return res


@app.patch("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: str, db: Session = Depends(get_db)):
    res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")
    res.status = ReservationStatus.cancelled
    db.commit()
    # Real system: this is the hook point to trigger a waitlist-refill workflow
    # (e.g. POST to an n8n webhook that texts waitlisted customers for this slot).
    return {"status": "cancelled", "confirmation_id": res.id}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@app.post("/orders")
def place_order(order: OrderCreate, db: Session = Depends(get_db)):
    if order.fulfillment not in ("pickup", "delivery"):
        raise HTTPException(status_code=400, detail="fulfillment must be 'pickup' or 'delivery'")
    if not order.items:
        raise HTTPException(status_code=400, detail="items cannot be empty")

    row = Order(
        name=order.name,
        phone=order.phone,
        items=", ".join(order.items),
        fulfillment=order.fulfillment,
        eta_minutes=25 if order.fulfillment == "pickup" else 40,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "status": "received",
        "order_id": row.id,
        "items": order.items,
        "eta_minutes": row.eta_minutes,
    }


@app.get("/orders/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)):
    row = db.query(Order).filter(Order.id == order_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


@app.get("/orders")
def list_orders(phone: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Order)
    if phone:
        q = q.filter(Order.phone == phone)
    return q.order_by(Order.created_at.desc()).all()


@app.patch("/orders/{order_id}/status")
def update_order_status(order_id: str, update: OrderStatusUpdate, db: Session = Depends(get_db)):
    row = db.query(Order).filter(Order.id == order_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    row.status = update.status
    db.commit()
    return {"order_id": row.id, "status": row.status}


@app.get("/health")
def health():
    return {"status": "ok"}
