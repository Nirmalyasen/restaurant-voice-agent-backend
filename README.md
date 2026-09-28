# Restaurant Voice Agent Backend

A FastAPI backend for a Retell AI restaurant voice assistant demo. Handles table
reservations (with real capacity checking) and to-go/delivery orders, persisted
to a database instead of in-memory state.

Built to be called directly from Retell AI custom functions — see
[Retell integration](#retell-ai-integration) below for the exact function configs.

## Features

- Real availability checking against booked covers per time slot (not a stub that
  always says "yes")
- Alternative time-slot suggestions when a requested slot is full
- Reservation cancellation, with a marked hook point for a waitlist-refill workflow
- Order placement and status tracking (`received → preparing → ready → completed`)
- SQLite by default (zero setup); one env var away from Postgres

## Quickstart

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env      # edit if you're using Postgres instead of SQLite

uvicorn main:app --reload --port 8000
```

API docs (interactive, auto-generated) are then at `http://localhost:8000/docs`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/availability?date=&time=&party_size=` | Check open covers for a slot; returns alternatives if full |
| POST | `/reservations` | Create a reservation |
| GET | `/reservations?date=&phone=` | List reservations, optionally filtered |
| GET | `/reservations/{id}` | Get one reservation |
| PATCH | `/reservations/{id}/cancel` | Cancel a reservation |
| POST | `/orders` | Place a pickup/delivery order |
| GET | `/orders/{id}` | Get one order (status, ETA) |
| GET | `/orders?phone=` | List orders, optionally filtered by phone |
| PATCH | `/orders/{id}/status` | Update order status (e.g. kitchen marks "ready") |
| GET | `/health` | Health check |

Dates are `YYYY-MM-DD`; times are `HH:MM` (24hr) on 30-minute slots.

## Retell AI integration

Configure these as **Custom Functions** on your Retell agent (Tools → + Add →
Custom Function). Base URL is wherever you deploy this (see below).

**`check_availability`** — GET `{BASE_URL}/availability`
```json
{
  "type": "object",
  "properties": {
    "date": {"type": "string", "description": "YYYY-MM-DD"},
    "time": {"type": "string", "description": "HH:MM, 24hr"},
    "party_size": {"type": "integer"}
  },
  "required": ["date", "time", "party_size"]
}
```

**`create_reservation`** — POST `{BASE_URL}/reservations`
```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "phone": {"type": "string"},
    "party_size": {"type": "integer"},
    "date": {"type": "string", "description": "YYYY-MM-DD"},
    "time": {"type": "string", "description": "HH:MM, 24hr"}
  },
  "required": ["name", "phone", "party_size", "date", "time"]
}
```

**`place_order`** — POST `{BASE_URL}/orders`
```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "phone": {"type": "string"},
    "items": {"type": "array", "items": {"type": "string"}},
    "fulfillment": {"type": "string", "enum": ["pickup", "delivery"]}
  },
  "required": ["name", "phone", "items", "fulfillment"]
}
```

For each function, remember to set `"type": "object"` at the schema's top level
(Retell rejects the save otherwise), and enable **Speak during execution** so
the agent says something like "Let me check that..." while the request runs.

## Deploying (Render / Railway)

1. Push this repo, connect it to Render or Railway
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. If you want the SQLite database to survive redeploys, attach a persistent
   disk and set `DATABASE_URL=sqlite:////data/restaurant.db` pointing at it —
   otherwise the filesystem resets on every deploy, which is fine for a quick
   demo but not for anything you're actually running a restaurant on
5. For real production use, switch to a managed Postgres add-on instead and
   set `DATABASE_URL` accordingly — no code changes needed, SQLAlchemy handles
   both

## Next steps

- Wire `PATCH /reservations/{id}/cancel` to an actual waitlist-refill workflow
  (e.g. call out to an n8n/Make webhook that texts waitlisted customers for
  that slot) — the hook point is marked with a comment in `main.py`
- Add a `waitlist` table if you want the refill logic to live in this service
  rather than an external automation tool
- Add auth (even a simple API key header) before exposing this publicly long-term

## License

MIT — see [LICENSE](LICENSE).
