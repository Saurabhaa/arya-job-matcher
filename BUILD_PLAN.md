# Arya Job Matcher — Build Plan

Build spec for Claude Code, structured for **phased execution** so it never runs out of context mid-build.

---

## How to use this doc with Claude Code

The build is split into **5 phases**. Each phase is a self-contained chunk that fits comfortably in a single Claude Code session. The flow per phase:

1. Start a fresh Claude Code session.
2. Paste the **Handoff prompt** for that phase (each phase below has one).
3. Claude Code reads `BUILD_PLAN.md`, executes only that phase, and stops at the DoD.
4. You verify the DoD checkboxes, run the smoke commands, and report back.
5. We move to the next phase.

Do not paste multiple phase prompts at once. Do not skip ahead. The DoD gates exist so we catch drift early — much cheaper than catching it during the video walkthrough.

---

## TL;DR

User uploads (or pastes) a resume. App returns the top 5 matching jobs with personalized reasoning, streamed progressively. Two-stage retrieval (pgvector shortlist → Claude reasoning), Redis caching on two layers, Sentry observability, full local run via `docker-compose up`.

---

## Assignment Requirements Coverage

Every line of the brief mapped to the phase that delivers it. Verify nothing's missing before starting.

| Requirement (from brief) | Phase |
|---|---|
| Input: resume as PDF upload | Phase 2 |
| Input: resume as pasted text | Phase 2 |
| 20+ job descriptions from `jobs.json` | Phase 1 |
| Top 5 ranked matches | Phase 3 |
| Per match: score, 2-3 line reasoning, one verbatim resume bullet | Phase 3 |
| Minimal web UI in React + TypeScript | Phase 4 |
| Upload or paste, trigger match, see results | Phase 4 |
| Results stream / progressive updates, no 30s block | Phase 2 + 3 |
| Backend: Python, FastAPI, async SQLAlchemy, PostgreSQL | All phases |
| LLM choice (Anthropic), justified | Phase 5 README |
| Agent framework (LangChain/LangGraph) — skipped, justified | Phase 5 README |
| Vector search via pgvector | Phase 1 + 2 |
| Caching via Redis, applied meaningfully | Phase 2 (embed) + Phase 3 (result) |
| Sentry with at least one meaningful custom transaction | Phase 4 |
| Working `docker-compose.yml` | Phase 1 |
| README: setup, architecture, what-I'd-do-with-a-week, known issues | Phase 5 |
| (Bonus) Deploy to Render/Railway/Fly | Cut by default; revisit if Phase 5 lands with slack |

---

## Stack (non-negotiable — do not substitute)

| Layer | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, async SQLAlchemy 2.0, asyncpg |
| DB | PostgreSQL 16 + pgvector extension |
| Cache | Redis 7 |
| LLM | Anthropic Claude Sonnet via official `anthropic` SDK (model: `claude-sonnet-4-6`, or the latest Sonnet at build time) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) |
| Observability | `sentry-sdk[fastapi]` with one custom transaction |
| Frontend | Vite + React 18 + TypeScript + Tailwind |
| Orchestration | docker-compose |

**Do not introduce:** LangChain, LangGraph, FAISS, WebSockets, Alembic, auth, user model, test coverage targets, CI pipelines.

---

## Repo Layout

```
.
├── docker-compose.yml
├── .env.example
├── init.sql                    # CREATE EXTENSION vector;
├── jobs.json                   # given dataset
├── README.md
├── BUILD_PLAN.md               # this file
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py             # FastAPI app, Sentry init, CORS, startup hooks
│   │   ├── config.py           # pydantic-settings
│   │   ├── schemas.py          # Pydantic IO models
│   │   ├── db/
│   │   │   ├── session.py      # async engine, get_session
│   │   │   └── models.py       # Job model with pgvector Vector column
│   │   ├── api/
│   │   │   └── match.py        # POST /api/match (SSE)
│   │   └── services/
│   │       ├── pdf.py          # pypdf text extraction
│   │       ├── embeddings.py   # OpenAI embed + Redis cache
│   │       ├── llm.py          # Claude wrapper + MATCHER_SYSTEM_PROMPT
│   │       ├── cache.py        # Redis client + helpers
│   │       └── matcher.py      # two-stage orchestrator
│   └── scripts/
│       └── seed_jobs.py        # one-shot: embed + upsert jobs.json
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx             # upload + paste + results
        ├── types.ts
        ├── lib/sse.ts          # typed fetch-based SSE consumer
        └── components/
            ├── ResumeInput.tsx # tabbed upload / paste
            └── MatchCard.tsx
```

---

## Environment Variables (.env.example)

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
SENTRY_DSN=
DATABASE_URL=postgresql+asyncpg://arya:arya@db:5432/arya
REDIS_URL=redis://redis:6379/0
CORS_ORIGINS=http://localhost:5173
CLAUDE_MODEL=claude-sonnet-4-6
EMBEDDING_MODEL=text-embedding-3-small
SHORTLIST_SIZE=10
LLM_CONCURRENCY=5
RESULT_CACHE_TTL_SECONDS=86400
```

---

## Locked Contracts (write first, do not change mid-build)

```python
# backend/app/schemas.py
from pydantic import BaseModel, Field
from typing import Literal, Any

class JobMatch(BaseModel):
    job_id: int
    title: str
    company: str
    score: int = Field(ge=0, le=100)
    reasoning: str          # 2-3 lines, plain English, concrete
    highlight_bullet: str   # verbatim resume bullet, no paraphrase

class ShortlistItem(BaseModel):
    job_id: int
    title: str
    company: str
    distance: float

class SSEEvent(BaseModel):
    type: Literal["shortlist", "match", "done", "error"]
    data: Any
```

```sql
-- init.sql
CREATE EXTENSION IF NOT EXISTS vector;
```

```python
# backend/app/db/models.py — Job model essentials
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase
from sqlalchemy import UniqueConstraint
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase): ...

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    company: Mapped[str]
    location: Mapped[str | None]
    description: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    __table_args__ = (UniqueConstraint("title", "company", name="uq_job"),)
```

---

## The Matcher System Prompt (lock early, do not edit mid-build)

```
You are a precise job-fit evaluator for a senior engineer's resume.

Given a RESUME and a JOB_DESCRIPTION, return JSON with three fields:

1. score (0-100): fit score. Anchor strictly:
   - 90+: candidate clearly exceeds requirements
   - 70-89: strong match, most requirements met
   - 50-69: partial match, meaningful gaps
   - <50: poor fit, fundamental mismatch
   Be honest. Don't inflate.

2. reasoning (2-3 lines): plain English, concrete. Reference specific skills,
   years of experience, or projects from the resume that map to the job's
   needs. No fluff, no flattery, no hedging like "could potentially". Down-rank
   when there's domain mismatch (e.g. backend engineer for frontend role),
   seniority mismatch, or missing required skills. Do not invent skills the
   resume doesn't show.

3. highlight_bullet: a single bullet pulled VERBATIM from the resume that
   would be the strongest line to feature in a cover letter for THIS specific
   role. Copy it word-for-word — do not paraphrase, do not summarize. Pick the
   bullet whose specifics (numbers, technologies, outcomes) align most
   closely with what the job is asking for.

Return ONLY the JSON object. No preamble, no explanation, no markdown fences.
```

Use Anthropic's tool-use feature with `JobMatch` as the tool input schema to guarantee structured output rather than parsing free text.

---

# Phase 1 — Foundation

**Goal:** Repo skeleton runs end-to-end; database is seeded with 20 jobs and their embeddings.

**Scope:** docker-compose, FastAPI skeleton, Vite skeleton, DB model, seed script.

### Tasks

- [ ] `docker-compose.yml` with services: `db` (`pgvector/pgvector:pg16`), `redis` (`redis:7-alpine`), `backend`, `frontend`. Mount `init.sql` to db's init dir.
- [ ] Backend `pyproject.toml` pins: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `pgvector`, `redis>=5`, `anthropic`, `openai`, `pypdf`, `sentry-sdk[fastapi]`, `pydantic-settings`, `python-multipart`, `sse-starlette`.
- [ ] Frontend: `npm create vite@latest frontend -- --template react-ts` then add Tailwind per the official Vite guide.
- [ ] Backend `GET /health` returns `{"status": "ok"}`. CORS allows `http://localhost:5173`.
- [ ] Define `Job` SQLAlchemy model per the locked contract.
- [ ] On app startup: `Base.metadata.create_all` on an async engine. No Alembic — document this as a scope call.
- [ ] `scripts/seed_jobs.py`: read `jobs.json`, embed `f"{title}\n\n{description}"` via OpenAI, upsert by `(title, company)`. Idempotent.
- [ ] Doc the seed command in README skeleton: `docker-compose exec backend python -m app.scripts.seed_jobs`.

### Definition of Done

- [ ] `docker-compose up` brings all four services green.
- [ ] `curl localhost:8000/health` returns 200 with the JSON body.
- [ ] `localhost:5173` shows the Vite default page.
- [ ] After seed, `SELECT count(*) FROM jobs WHERE embedding IS NOT NULL;` returns ≥20.
- [ ] Re-running seed does not duplicate rows.

### Handoff prompt for Claude Code

> Read `BUILD_PLAN.md` in full. Execute **Phase 1 only**. Stop after every DoD checkbox in Phase 1 passes. Do not start Phase 2. Run the verification commands listed in the DoD and paste their output at the end of your report. Commit at the end with message `Phase 1: foundation + seed`.

---

# Phase 2 — Retrieval (Stage 1) + SSE Plumbing

**Goal:** A real shortlist of 10 jobs streams back over SSE, given either a PDF upload or pasted text. No LLM reasoning yet — that's Phase 3.

**Scope:** `/api/match` endpoint, PDF + paste paths, embedding cache, pgvector cosine query, SSE event plumbing.

### Tasks

- [ ] `services/pdf.py`: extract text via pypdf, strip excess whitespace.
- [ ] `POST /api/match` accepts either:
  - `file: UploadFile` (PDF), OR
  - `resume_text: str` via Form,
  - returns 400 if neither is provided, 400 if file is non-PDF.
- [ ] Response is `EventSourceResponse` (from `sse-starlette`), `media_type="text/event-stream"`.
- [ ] Compute `resume_hash = sha256(resume_text).hexdigest()` — used for all downstream cache keys.
- [ ] `services/embeddings.py::embed(text)`: OpenAI call, Redis-cached under `embed:{sha256(text)}`, 7-day TTL.
- [ ] `services/matcher.py::shortlist(resume_text, k=10)`:
  - Embed the resume (cache-aware).
  - SQL: `SELECT *, embedding <=> :vec AS distance FROM jobs ORDER BY embedding <=> :vec LIMIT :k`.
  - Return list of (Job, distance) tuples.
- [ ] Wire `asyncio.Queue` orchestration: a background task puts events, the SSE generator yields them. For Phase 2 the only events are `shortlist` and a stubbed `done`.
- [ ] Emit `{"type": "shortlist", "data": [ShortlistItem, ...]}` then `{"type": "done", "data": {}}`.

### Definition of Done

- [ ] `curl -N -X POST -F "file=@resume.pdf" http://localhost:8000/api/match` streams a `shortlist` event with 10 items, then a `done` event.
- [ ] `curl -N -X POST -F "resume_text=..." http://localhost:8000/api/match` works the same way.
- [ ] Same input → same 10 candidates, same order (deterministic).
- [ ] Second call with the same resume completes the embed step from cache (verify via Redis `KEYS embed:*` showing the key, or log a cache-hit line).

### Handoff prompt for Claude Code

> Read `BUILD_PLAN.md` in full and inspect the current repo state. Phase 1 is already complete. Execute **Phase 2 only**. Stop after every DoD checkbox in Phase 2 passes. Do not start Phase 3. Run the verification curl commands and paste their output. Commit with `Phase 2: pgvector shortlist + SSE plumbing`.

---

# Phase 3 — Reasoning (Stage 2) + Result Cache

**Goal:** Full pipeline. 10 Claude calls run in parallel, each result streams as it arrives, top 5 settle in a final `done` event. Re-runs are instant.

**Scope:** Claude wrapper with tool-use structured output, parallel orchestration with `asyncio.as_completed`, result cache layer, token accounting.

### Tasks

- [ ] `services/llm.py::match_job(resume_text, job)`:
  - Anthropic SDK call to `CLAUDE_MODEL`.
  - System prompt: the locked `MATCHER_SYSTEM_PROMPT`.
  - Tool-use: define a tool whose `input_schema` is the `JobMatch` Pydantic schema; force `tool_choice` to that tool to get guaranteed structured output.
  - Return parsed `JobMatch` + token usage from the response object.
- [ ] `services/cache.py`: helpers `get_match_cache(resume_hash, job_id)` and `set_match_cache(resume_hash, job_id, match)`. Key shape: `match:{resume_hash}:{job_id}`. TTL from `RESULT_CACHE_TTL_SECONDS`.
- [ ] Cache-wrap `match_job`: check cache → return on hit → call Claude on miss → write back.
- [ ] Orchestrator updates:
  - `sem = asyncio.Semaphore(LLM_CONCURRENCY)`.
  - Launch one task per shortlisted job; each task `async with sem:` then calls `match_job`.
  - Use `asyncio.as_completed` (not `gather`) so results push into the SSE queue *as they finish*, not all at once.
  - Track running counters: `cache_hits`, `cache_misses`, `total_tokens_in`, `total_tokens_out`.
  - After all 10 settle, sort by `score` desc, take top 5, emit `{"type": "done", "data": {"top5": [JobMatch, ...], "stats": {...}}}`.
- [ ] If a single Claude call fails: log the error, emit `{"type": "error", "data": {"job_id": N, "message": "..."}}` for that job, continue the rest. Do not break the stream.

### Definition of Done

- [ ] First run on a fresh resume: `shortlist` event within ~2s → 10 `match` events arriving visibly staggered → `done` event with 5 sorted results. Total time 5-15s.
- [ ] Second run on the same resume: same flow but completes in <1s (all 10 cache hits, verifiable via the counters in the `done` event stats).
- [ ] Top-5 ordering is by `score` desc.
- [ ] Each `JobMatch.highlight_bullet` is a verbatim substring of the resume text (no paraphrase). Verify manually on at least 3 results.
- [ ] Force an error (e.g. invalid API key) on one job — the other 9 still succeed, one `error` event surfaces.

### Handoff prompt for Claude Code

> Read `BUILD_PLAN.md` in full and inspect the current repo state. Phases 1 and 2 are complete. Execute **Phase 3 only**. Stop after every DoD checkbox in Phase 3 passes. Do not start Phase 4. Run the verification commands; for the verbatim-bullet check, include the resume text and the three `highlight_bullet` values in your report so I can eyeball them. Commit with `Phase 3: parallel Claude reasoning + result cache`.

---

# Phase 4 — Observability + Frontend

**Goal:** Full browser demo works end to end. Sentry shows one rich transaction per match flow.

**Scope:** Sentry custom transaction with tags and per-job spans, React UI with upload/paste tabs, typed SSE consumer, progressive cards.

### Tasks — Sentry

- [ ] Init Sentry in `main.py`: `traces_sample_rate=1.0` for the demo (note in README this would be ~0.1 in prod).
- [ ] Wrap the orchestrator in `with sentry_sdk.start_transaction(op="match", name="match_resume_to_jobs") as txn:`.
- [ ] Per-job spans inside the transaction (`txn.start_child(op="llm.match_job", description=f"job_id={job.id}")`) so per-call latency is visible.
- [ ] Set transaction tags at finish: `shortlist_size`, `cache_hits`, `cache_misses`, `total_tokens_in`, `total_tokens_out`, `estimated_usd` (compute from Sonnet's published per-token prices), `resume_hash_prefix` (first 8 chars only).

### Tasks — Frontend

- [ ] `src/types.ts`: TypeScript mirrors of `JobMatch`, `ShortlistItem`, `SSEEvent`.
- [ ] `lib/sse.ts::streamMatch(input, onEvent)`:
  - `input` can be `{ file: File }` or `{ text: string }`.
  - Use `fetch` + `response.body!.getReader()`. **Not** `EventSource` — doesn't support POST/multipart.
  - Parse the SSE format manually: buffer text, split on `\n\n`, parse `data: {...}` lines as JSON, call `onEvent`.
- [ ] `components/ResumeInput.tsx`: simple tabbed UI — "Upload PDF" (file picker) and "Paste text" (textarea). Both wired to the same `streamMatch` call.
- [ ] `components/MatchCard.tsx`: title, company, score badge (green 80+, blue 60-79, gray <60), 2-3 line reasoning, `highlight_bullet` rendered in italics with a `↳ Highlight in cover letter:` label.
- [ ] `App.tsx` flow:
  - On `shortlist` event: render 10 skeleton cards with title + company filled, score/reasoning as a shimmer.
  - On each `match` event: find the card by `job_id`, swap skeleton → real content.
  - On `done` event: filter to the 5 `job_id`s in `data.top5`, fade out the other 5.
  - Style with Tailwind only. Plain. No icon libraries, no animation libraries.

### Definition of Done

- [ ] Open `localhost:5173`. Upload a PDF resume. See 10 skeletons appear within ~2s, real cards stream in, top 5 settle. Total <15s.
- [ ] Paste resume text in the textarea, click Match — same flow works.
- [ ] In Sentry: one transaction per match flow, named `match_resume_to_jobs`, with all tags populated and 10 child spans visible.
- [ ] Second run on the same resume completes in <1s and shows cache_hits=10 in the Sentry tags.

### Handoff prompt for Claude Code

> Read `BUILD_PLAN.md` in full and inspect the current repo state. Phases 1, 2, and 3 are complete. Execute **Phase 4 only**. Stop after every DoD checkbox in Phase 4 passes. Do not start Phase 5. Include a screenshot of the Sentry transaction (or its tag list pasted as text) in your report. Commit with `Phase 4: Sentry transaction + React frontend`.

---

# Phase 5 — Docs + Smoke Test

**Goal:** Submission-ready repo. README is publishable. Fresh clone runs in 5 commands.

**Scope:** README content, scope notes, final polish pass, fresh-clone verification.

### Tasks

- [ ] README sections (exactly per the brief):

  **Setup:** `git clone …` → `cp .env.example .env` → fill 3 API keys → `docker-compose up -d` → seed command → open `localhost:5173`. Show real commands, not prose.

  **Architecture:** treat this as a design doc — the brief weights it heaviest. Cover:
  - The two-stage retrieval pattern and why (cost-aware thinking; would generalize to N=20k).
  - Why Claude Sonnet (structured output via tool-use, strong reasoning per dollar, streaming-friendly).
  - Why no LangChain/LangGraph (brief explicitly invites skipping; retrieve→fan-out→reduce is ~30 lines of asyncio).
  - Why SSE over WebSocket (one-way push, HTTP-native, no reverse channel).
  - Why two cache layers, and the exact keys.
  - The Sentry transaction design and what each tag is for.
  - Embed the architecture diagram (link to or include the SVG).

  **What I'd do differently with one more week:**
  - HNSW or IVF-Flat index on the vector column (currently seq-scan, fine at 20 rows).
  - Hybrid BM25 + vector retrieval with reciprocal rank fusion.
  - Eval harness: gold-standard resume-job pairs and score correlation tracking.
  - Prompt versioning + A/B testing infra.
  - Per-tenant cache namespacing.
  - Stream Claude's token output to give in-token reasoning preview.
  - Rate limiting + retry/backoff on Claude 429s.
  - Replace pypdf with a layout-aware parser (Unstructured.io, LlamaParse) for tabular resumes.
  - Move to a larger embedding model (`text-embedding-3-large` or `voyage-3`) if shortlist precision matters more than cost.

  **Known issues / limitations / chose-not-to-fix:**
  - pypdf can't handle scanned/image-based PDFs.
  - No rate limiting on `/api/match`.
  - No retry/backoff on upstream API errors — they surface as `error` events.
  - Models not configurable per-request.
  - `jobs.json` loaded only via seed script; no admin UI.
  - 24h result cache TTL is arbitrary.
  - DB schema via `metadata.create_all` on startup (no Alembic) — fine for demo, not for prod.

- [ ] Quick polish pass: remove dead code, kill `console.log`s, run `ruff check --fix` on backend.
- [ ] Fresh-clone smoke test: rename the repo dir, re-clone in a temp folder, follow only the README, verify the demo works.

### Definition of Done

- [ ] Fresh clone → follow README only → working browser demo in ≤5 commands.
- [ ] README architecture section is at least 500 words and reads like a design doc, not a setup guide.
- [ ] All four required README sections present and headed clearly.
- [ ] No dead files, no commented-out blocks, no `TODO` in committed code (move them to a "Known issues" bullet instead).

### Handoff prompt for Claude Code

> Read `BUILD_PLAN.md` in full and inspect the current repo state. Phases 1 through 4 are complete. Execute **Phase 5 only**. Spend the bulk of the time on the README architecture section — treat it as the most important deliverable. After polishing, do the fresh-clone smoke test yourself by `cp -r`-ing the repo to `/tmp/smoke` and running only the README commands. Report results. Final commit: `Phase 5: README + polish`.

---

## Claude Code Working Rules (always apply, every phase)

1. **Execute one phase per session.** Do not skip ahead, do not go back.
2. **Use the exact stack listed.** Do not substitute. Stop and ask if a substitution feels necessary.
3. **Lock contracts first** in any phase that introduces them. `schemas.py`, system prompt, SSE event shape — write them before logic.
4. **Commit at phase boundary** with the message specified in the phase Handoff prompt.
5. **Keep files small.** If a file passes ~300 lines, split it along service boundaries.
6. **Surface assumptions.** When ambiguity hits, write a one-line note in README under "Scope calls" and continue — do not paralyze.
7. **No tests beyond a smoke test per layer.** Brief explicitly excludes coverage targets.
8. **No premature optimization.** No connection pool tuning, no IVF index, no Claude response token streaming.
9. **The README architecture section is the single most important deliverable.** Per the brief. Phase 5 is not boilerplate.
10. **Stop and ask** if: contracts need to change mid-build, the brief contradicts itself, or a phase DoD can't be met without dropping another constraint.

---

## Out of scope (do not build)

- User auth, accounts, sessions
- Database migrations (Alembic)
- CI/CD, GitHub Actions, lint hooks
- Test coverage targets
- UI polish beyond Tailwind defaults
- Deployment to Render/Fly/Railway (revisit only if all 5 phases land with slack)
- Multi-tenant features, workspace isolation
- Admin / jobs management UI
- Streaming Claude's token output (in-token reasoning preview)
- Rate limiting, retry/backoff on upstream APIs
