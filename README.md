# Multi-Agent Research API — Phase 1 Backend

FastAPI foundation for the ResearchFlow multi-agent research & report generator.

This Phase 1 backend provides health checks, research run CRUD (in-memory), and
placeholder plan generation. It is structured so OpenRouter, LangGraph, Supabase,
and SSE can be added later without rewriting the API surface.

The existing Next.js app in `../researchflow/` is unchanged by this package.

## Technology stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 + pydantic-settings
- httpx (ready for future provider calls)
- pytest

**Not included in Phase 1:** LangGraph, CrewAI, Celery, Redis, SQLAlchemy, real OpenRouter calls.

## Folder structure

```text
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, exception handlers
│   ├── core/                # config, logging, exceptions
│   ├── api/v1/              # health, runs, plans routers
│   ├── schemas/             # request/response models
│   ├── services/            # business logic
│   ├── repositories/        # Phase 1 in-memory store
│   ├── agents/              # placeholder for future agents
│   └── utils/
├── tests/
├── requirements.txt
├── .env.example
└── README.md
```

## Environment setup

```bash
cd backend
cp .env.example .env
```

Edit `.env` as needed. OpenRouter remains optional for Phase 1.

### Database migrations (runs table)

1. Copy the URI from Supabase → **Project Settings → Database → Connection string**
2. Set `DATABASE_URL` in `backend/.env`
3. Apply:

```bash
cd backend
source venv/bin/activate
python scripts/apply_migrations.py
```

Or paste `migrations/001_create_runs.sql` into the Supabase **SQL Editor** and run it.

Confirm with `GET /api/v1/health` → `supabase.runs_table: true`.

### Supabase

Set these in `backend/.env` (already listed in `.env.example`):

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY` (publishable / anon)
- `SUPABASE_SERVICE_ROLE_KEY` (secret — **server only**, never expose to Next.js)
- `DATABASE_URL` (Postgres URI — required to apply SQL migrations)

On startup the API creates a server-side Supabase client and probes
`/auth/v1/health`. Check status via `GET /api/v1/health` → `data.supabase`.

Phase 1 run APIs persist to the `runs` table via Router → Service → Repository → Supabase.

Never commit real API keys.

## Virtual environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Run development server

From the `backend/` directory (so `app` is importable):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- App root: http://localhost:8000
- OpenAPI docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health

## Run tests

```bash
cd backend
source venv/bin/activate
pytest -q
```

Tests use in-memory storage and do not require a database.

## Phase 1 endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info |
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/runs` | Create research run (`plan_pending`) |
| GET | `/api/v1/runs` | List runs (`status`, `limit`, `offset`) |
| GET | `/api/v1/runs/{run_id}` | Get run |
| POST | `/api/v1/runs/{run_id}/cancel` | Cancel run |
| POST | `/api/v1/runs/{run_id}/plan/generate` | Placeholder plan (3/5/8 sections by depth) |
| GET | `/api/v1/runs/{run_id}/plan` | Get plan |
| PUT | `/api/v1/runs/{run_id}/plan` | Update edited plan |

## Response envelope

Success:

```json
{ "success": true, "message": "...", "data": {} }
```

Error:

```json
{ "success": false, "message": "...", "detail": {} }
```

## CORS

Configured for the Next.js frontend via `FRONTEND_URL` / `CORS_ORIGINS`
(defaults include `http://localhost:3000`). No wildcard origins in development.

## Architecture notes

```text
Router → Service → Repository (in-memory today)
```

Placeholder plan generation is explicitly separated from future AI planner logic
in `app/services/planner_service.py` (`build_placeholder_plan`).

## Next phases (not implemented)

Planner Agent → Human Approval → Parallel Research → Writer → Verifier → Report,
plus OpenRouter, LangGraph, PostgreSQL/Supabase, and SSE streaming.
