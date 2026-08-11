# ⚡ AI Electrician

A self-hosted system for storing, organizing, and **troubleshooting** industrial
electrical schematics (1-line / ladder prints) with a local AI assistant.

Upload your PDF prints, organize them by machine, and ask plain-English questions
like *"cutter motor fails to start"* — the assistant answers with specific,
**grounded** guidance drawn from your own prints:

> Check for 120V control voltage between wire **A1** and ground. Confirm motor
> contactor **M3** is pulled in, and inspect overload **OL3** for a trip.
> _[Line 3 Cutter — Sheet 1, p1]_

Every answer cites the exact print and page so a technician can open the schematic
and **verify** it. All AI runs against **your own Ollama server** — no data leaves
the shop.

---

## What it does

- **Organize by machine** — machines are the top-level unit; prints belong to a machine.
- **Upload / delete prints** — drag-drop PDFs. Vector CAD exports *and* scanned
  images are supported (a text layer is used when present; OCR runs automatically for scans).
- **AI reads the prints** — on upload, a vision model extracts components
  (M3, CR1, CB2…), wire numbers (A1, X2…), voltages, and connections into a
  structured, searchable model.
- **Troubleshoot by chat** — machine-scoped Q&A with streamed answers and clickable citations.
- **View & search** — a page viewer with zoom, full-text/wire-number search, and
  entity overlays, so you can confirm what the AI tells you.
- **Train by correcting** — fix any mis-read component or wire, and mark answers
  right/wrong. Corrections and confirmed answers feed a per-machine knowledge base
  that makes future answers better — no model retraining required.

## Architecture

```
Browser ──▶ web (React/nginx) ──▶ api (FastAPI) ──▶ Postgres + pgvector
                                        │                  ▲
                                        ├── Redis ──▶ worker (PDF + AI extraction)
                                        └───────────────▶ Ollama (your LLM server)
```

| Service  | Role                                                             |
|----------|------------------------------------------------------------------|
| `web`    | React SPA served by nginx; proxies `/api` to the backend         |
| `api`    | FastAPI: CRUD, upload, search, chat, corrections                 |
| `worker` | ARQ background jobs: render pages, OCR, vision extraction, embeddings |
| `db`     | PostgreSQL 16 + pgvector (structured data + RAG embeddings + full-text) |
| `redis`  | job queue                                                        |
| Ollama   | **runs separately** — your local LLM server (address set in `.env`) |

---

## Quick start

**Requirements:** access to an Ollama server. The installer sets up Docker for you.

### One-command install (recommended)

**Ubuntu / Debian server:**
```bash
git clone <this repo>
cd ai_electrician
sudo ./scripts/install-ubuntu.sh
```
Installs Docker if missing, generates a secure `.env` (random DB password + secret,
prompts for the Ollama URL and optional app password), builds and starts the stack,
and verifies the API + Ollama connection.

**Windows server (PowerShell, run as Administrator):**
```powershell
git clone <this repo>
cd ai_electrician
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install-windows.ps1
```
Checks for Docker Desktop (installs via `winget` if missing — you then start Docker
Desktop once and re-run), generates `.env`, and brings the stack up.

> Both installers are **idempotent**: re-running them never overwrites an existing
> `.env`, so your database password stays stable across upgrades. To change settings
> later, edit `.env` and run `docker compose up -d --build`.

### Manual install

```bash
cp .env.example .env
# edit .env — at minimum set OLLAMA_BASE_URL and the model names (see below)
docker compose up -d --build
```

Then open **http://localhost:8080** (or `http://<server-ip>:8080` from a shop tablet).

- API docs: `http://localhost:8000/docs`
- Ollama connectivity check: the header shows an **Ollama** status pill; details at
  `http://localhost:8000/api/ollama/status`.

### Point it at your Ollama server

Set these in `.env`:

```ini
# If Ollama runs on the same host as Docker:
OLLAMA_BASE_URL=http://host.docker.internal:11434
# If Ollama runs on another machine on the LAN:
# OLLAMA_BASE_URL=http://192.168.1.50:11434

OLLAMA_CHAT_MODEL=gpt-oss:120b        # reasoning / troubleshooting answers
OLLAMA_VISION_MODEL=qwen2.5-vl:72b    # reads the schematics during extraction
OLLAMA_EMBED_MODEL=nomic-embed-text   # search embeddings
EMBED_DIM=768                         # must match the embed model (nomic-embed-text = 768)
```

Pull the models on your Ollama server first, e.g.:

```bash
ollama pull gpt-oss:120b
ollama pull qwen2.5-vl:72b
ollama pull nomic-embed-text
```

> **Model choice (≤120B):** `gpt-oss:120b` is a strong reasoner for troubleshooting.
> For vision, any capable open VLM works (`qwen2.5-vl:72b`, `llama3.2-vision:90b`, …).
> To simplify ops you can set `OLLAMA_CHAT_MODEL` and `OLLAMA_VISION_MODEL` to the
> **same** capable vision model. If you change the embed model, update `EMBED_DIM`
> to match and re-create the DB (the vector column dimension is fixed at migration).

---

## Using it

1. **Create a machine** (e.g. "Line 3 Cutter").
2. **Upload prints** (PDF) under that machine. Processing runs in the background —
   status goes `processing → ready`, or `review` if the AI flagged low-confidence reads.
3. **Review** (optional but recommended): open a print, use the *Review* panel to fix
   any wrong designators/wire numbers/voltages and click **Verify**. This directly
   improves the assistant.
4. **Troubleshoot**: open the machine's *Troubleshoot* tab and describe the problem.
   Click a citation chip to jump to the exact page and confirm.
5. **Correct answers**: 👍 marks an answer helpful (saved as verified guidance); 👎 lets
   you type the correct guidance. Both feed the machine's **Knowledge Base**.

### How "training" works (important)

This system does **not** fine-tune the LLM's weights. Instead it improves through a
**grounding + correction loop**:

- Correcting extracted components/wires improves the *facts* the AI reasons over.
- Confirmed-good answers and typed corrections become verified **Knowledge Base**
  entries, retrieved as authoritative context on future matching questions.

The result: the assistant gets measurably better per machine over time, safely and
locally, without ever retraining a model.

---

## Operations

- **Logs:** `docker compose logs -f api worker`
- **Restart:** `docker compose restart api worker`
- **Backup:** `./scripts/backup.sh` (dumps the DB + uploaded prints to `./backups`)
- **Stop:** `docker compose down` (add `-v` to also delete data volumes — destructive)

### Optional LAN auth
Off by default (trusted shop LAN). To require a shared password:

```ini
AUTH_ENABLED=true
AUTH_SHARED_PASSWORD=your-shop-password
AUTH_SECRET=<long-random-string>
```

---

## Development (without Docker)

Backend:
```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# needs Postgres+pgvector and Redis reachable; set env vars or a local .env
alembic upgrade head
uvicorn app.main:app --reload
# in another shell: arq app.worker.WorkerSettings
```

Frontend:
```bash
cd frontend
npm install
VITE_API_TARGET=http://localhost:8000 npm run dev   # http://localhost:5173
```

---

## Repository layout

```
backend/            FastAPI app, worker, migrations
  app/
    models.py       data model (machines, prints, components, wires, KB, chat…)
    pdf_processing.py  render + text/OCR extraction
    extraction.py   vision-model structured extraction
    indexing.py     end-to-end ingestion pipeline (worker)
    rag.py          hybrid retrieval + grounded prompt
    ollama_client.py  the single point of LLM integration
    routers/        machines, prints, search, chat, review, system
  alembic/          database migrations (enables pgvector, builds schema+indexes)
frontend/           React + TypeScript SPA (Vite), served by nginx
scripts/backup.sh   database + files backup
docker-compose.yml  full local stack
```

## Safety note
Troubleshooting guidance is an aid, not a substitute for qualified judgment. Always
follow lockout/tagout and PPE procedures before taking live measurements.
