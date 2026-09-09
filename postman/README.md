# Postman API testing

## Import (recommended)

Import this single collection:

`backend/postman/ResearchFlow_Full_API.postman_collection.json`

Also available:
- `ResearchFlow_Phase1_Runs.postman_collection.json`
- `ResearchFlow_Phase2_Plans.postman_collection.json`
- `ResearchFlow_Phase3_Sections.postman_collection.json`
- `ResearchFlow_Phase4_Research.postman_collection.json`
- `ResearchFlow_Phase5_Research.postman_collection.json`
- `ResearchFlow_Phase6_Events.postman_collection.json`
- `ResearchFlow_Phase7_Reports.postman_collection.json`
- `ResearchFlow_Phase8_Verification.postman_collection.json`
- `ResearchFlow_Phase9_Usage_Export.postman_collection.json`
- `ResearchFlow_Phase10_Reliability.postman_collection.json`

## Before you test

1. Restart the backend after editing `.env` (settings are cached):

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

2. Confirm health:

`GET http://localhost:8000/api/v1/health`

Expect:

- `runs_table`: true
- `plan_sections_table`: true
- `openrouter_configured`: true

If `openrouter_configured` is false, set `OPENROUTER_API_KEY` in `backend/.env` and restart uvicorn.

## Test order

| # | Request | Method | URL |
|---|---|---|---|
| 1 | Health | GET | `/api/v1/health` |
| 2 | Create Run | POST | `/api/v1/runs` |
| 3 | List Runs | GET | `/api/v1/runs` |
| 4 | Get Run | GET | `/api/v1/runs/{{runId}}` |
| 5 | Get Status | GET | `/api/v1/runs/{{runId}}/status` |
| 6 | Generate Plan (AI) | POST | `/api/v1/runs/{{runId}}/plan/generate` |
| 7 | Get Plan | GET | `/api/v1/runs/{{runId}}/plan` |
| 8 | Update Plan | PUT | `/api/v1/runs/{{runId}}/plan` |
| 9 | Improve Plan (AI) | POST | `/api/v1/runs/{{runId}}/plan/improve` |
| 10 | Approve Plan | POST | `/api/v1/runs/{{runId}}/plan/approve` |

`POST Create Run` auto-saves `runId`.

## Bodies

### Create Run

```json
{
  "topic": "Future of Artificial Intelligence in Healthcare",
  "depth": "standard",
  "audience": "technical",
  "tone": "professional",
  "target_length": 3000,
  "source_types": ["official", "research", "news"]
}
```

### Improve Plan

```json
{
  "instructions": "Make the plan more technical"
}
```

Header for JSON bodies: `Content-Type: application/json`

## Phase 3 — Sections

Import: `ResearchFlow_Phase3_Sections.postman_collection.json`

| Method | Path |
|--------|------|
| POST | `/api/v1/runs/{run_id}/sections` |
| GET | `/api/v1/runs/{run_id}/sections` |
| GET | `/api/v1/runs/{run_id}/sections/{section_id}` |
| PUT | `/api/v1/runs/{run_id}/sections/{section_id}` |
| DELETE | `/api/v1/runs/{run_id}/sections/{section_id}` |
| PUT | `/api/v1/runs/{run_id}/sections/reorder` |

Apply `migrations/003_update_plan_section_statuses.sql` before using status `researched`.

Reorder body:

```json
{
  "section_ids": ["uuid-1", "uuid-2", "uuid-3"]
}
```

## Phase 4 — Research tools & sources

Import: `ResearchFlow_Phase4_Research.postman_collection.json`

| Method | Path |
|--------|------|
| POST | `/api/v1/research/search` |
| POST | `/api/v1/research/fetch` |
| POST | `/api/v1/research/extract` |
| GET | `/api/v1/runs/{run_id}/sources` |
| GET | `/api/v1/runs/{run_id}/sources/{source_id}` |
| POST | `/api/v1/runs/{run_id}/sources` |
| DELETE | `/api/v1/runs/{run_id}/sources/{source_id}` |

Apply `migrations/004_create_sources.sql` before source CRUD.

Add source body:

```json
{ "url": "https://www.nih.gov/news-events" }
```

## Phase 5 — Multi-agent research & findings

Import: `ResearchFlow_Phase5_Research.postman_collection.json`

Apply `migrations/005_create_findings_and_research_tasks.sql` first. Health should show `findings_table` and `research_tasks_table` true.

| Method | Path |
|--------|------|
| POST | `/api/v1/runs/{run_id}/research/start` |
| GET | `/api/v1/runs/{run_id}/research/status` |
| GET | `/api/v1/runs/{run_id}/research/results` |
| POST | `/api/v1/runs/{run_id}/research/retry` |
| GET | `/api/v1/runs/{run_id}/findings` |
| GET | `/api/v1/runs/{run_id}/findings/{finding_id}` |
| GET | `/api/v1/runs/{run_id}/sections/{section_id}/findings` |
| POST | `/api/v1/runs/{run_id}/findings` |
| PUT | `/api/v1/runs/{run_id}/findings/{finding_id}` |
| DELETE | `/api/v1/runs/{run_id}/findings/{finding_id}` |

Start requires run status `approved` with section sub-questions. Research runs in the background; poll status until `completed` or `partial`.

## Phase 6 — Agent events & SSE

Import: `ResearchFlow_Phase6_Events.postman_collection.json`

Apply `migrations/006_create_agent_events.sql` first. Health should show `agent_events_table: true`.

| Method | Path |
|--------|------|
| GET | `/api/v1/runs/{run_id}/agents` |
| GET | `/api/v1/runs/{run_id}/agents/status` |
| GET | `/api/v1/runs/{run_id}/agents/{agent_name}` |
| GET | `/api/v1/runs/{run_id}/events` |
| GET | `/api/v1/runs/{run_id}/stream` |

Events query params: `event_type`, `agent_name`, `after_id`, `limit`, `offset`.

SSE stream: `Content-Type: text/event-stream`. Supports `?replay=true|false`, `?after_id=`, and `Last-Event-ID` for reconnect without duplicates.

```bash
curl -N -H 'Accept: text/event-stream' \
  'http://localhost:8000/api/v1/runs/<runId>/stream?replay=true'
```

## Phase 7 — Writer & reports

Import: `ResearchFlow_Phase7_Reports.postman_collection.json`

Apply `migrations/007_create_reports_and_writer_events.sql` first. Health should show `reports_table: true`.

| Method | Path |
|--------|------|
| POST | `/api/v1/runs/{run_id}/report/generate` |
| GET | `/api/v1/runs/{run_id}/report` |
| PUT | `/api/v1/runs/{run_id}/report` |
| POST | `/api/v1/runs/{run_id}/sections/{section_id}/generate` |
| POST | `/api/v1/runs/{run_id}/sections/{section_id}/regenerate` |

Regenerate body (optional):

```json
{ "instructions": "Make this section more technical" }
```

Full report generation calls OpenRouter per section — increase Postman timeout.

## Phase 8 — Verifier & quality

Import: `ResearchFlow_Phase8_Verification.postman_collection.json`

Apply `migrations/008_verification_fields_and_events.sql` first.

| Method | Path |
|--------|------|
| POST | `/api/v1/runs/{run_id}/verify` |
| GET | `/api/v1/runs/{run_id}/verification` |
| GET | `/api/v1/runs/{run_id}/conflicts` |

Requires an existing report. Verification updates `sections_confidence_json` (`high`/`medium`/`low`), `unverified_claims_json`, and `conflicts_json` without removing unsupported claims from markdown.

## Phase 9 — Usage, export, history & replay

Import: `ResearchFlow_Phase9_Usage_Export.postman_collection.json`

No new migration. Aggregates `agent_events` (tokens/cost from OpenRouter when present — never invented) and exports from `reports.markdown`.

| Method | Path |
|--------|------|
| GET | `/api/v1/runs` (history list: topic, status, duration, cost, source_count, dates) |
| GET | `/api/v1/runs/{run_id}/usage` |
| GET | `/api/v1/runs/{run_id}/cost` |
| GET | `/api/v1/runs/{run_id}/history` (summary + timeline from stored events) |
| GET | `/api/v1/runs/{run_id}/export?format=md` |
| GET | `/api/v1/runs/{run_id}/export?format=pdf` |
| GET | `/api/v1/runs/{run_id}/export?format=docx` |

Timeline replay reuses stored `agent_events` (no duplicate event storage). Save binary PDF/DOCX responses as files in Postman.

## Phase 10 — Reliability & final QA

Import: `ResearchFlow_Phase10_Reliability.postman_collection.json`

Apply `migrations/009_reliability_cancel_cost.sql` first. Health should show `reliability` config (`tool_timeout_s`, `run_timeout_s`, `run_cost_limit_usd`, …).

| Method | Path | Notes |
|--------|------|--------|
| POST | `/api/v1/runs` | Optional `cost_limit_usd` |
| POST | `/api/v1/runs/{run_id}/cancel` | Cooperative cancel; preserves findings |
| GET | `/api/v1/health` | Includes reliability settings |

Env (see `.env.example`): `RESEARCH_TOOL_TIMEOUT_S=30`, `RESEARCH_TOOL_MAX_RETRIES=2`, `RUN_TIMEOUT_S`, `RUN_COST_LIMIT_USD`, `OPENROUTER_MAX_RETRIES`, `RESEARCH_MAX_WORKERS`.

Status transitions enforced: `plan_pending → plan_ready → approved → running → completed` with alternatives `running → failed|cancelled|partial`.

## Notes

- Generate / Improve call OpenRouter and can take 30–120s on free models — increase Postman timeout if needed.
- Swagger alternative: http://localhost:8000/docs
