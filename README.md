# Arya Job Matcher

Upload or paste a resume; the app returns the top 5 matching jobs with personalized
reasoning and one verbatim resume bullet to feature per match. Results stream
progressively over SSE — no 30-second wall of nothing — and are cached so a
re-run is effectively free.

Stack: FastAPI · async SQLAlchemy 2.0 · PostgreSQL 16 + pgvector · Redis 7 ·
OpenAI (`gpt-5.4-mini` for reasoning, `text-embedding-3-small` for embeddings) ·
Sentry · Vite + React 18 + TypeScript + Tailwind · docker-compose.

---

## Setup

Five commands from a fresh clone to a working browser demo:

```bash
git clone <this-repo> arya && cd arya
cp .env.example .env                # fill OPENAI_API_KEY (required) + SENTRY_DSN (optional)
docker compose up -d --build
docker compose exec backend python -m app.scripts.seed_jobs
open http://localhost:5173          # or visit it in your browser
```

The backend health endpoint is `http://localhost:8001/health`. The frontend talks
to it directly (CORS allows `http://localhost:5173`).

### Note: host port 8001

Port `8000` was already taken on the dev machine during the build, so
[docker-compose.yml](docker-compose.yml) maps the backend as `8001:8000` (host:container).
Container-side the backend still listens on `8000`; only the host port shifted.
The frontend's SSE client and all curl commands in this README use `8001`. To
revert: change the backend service's `ports` to `"8000:8000"` in
`docker-compose.yml` and update `API_URL` in [frontend/src/lib/sse.ts](frontend/src/lib/sse.ts).

---

## Architecture

The brief weights the architecture section heaviest, so this section treats the
design as the primary deliverable rather than the code.

### Pipeline at a glance

```
  Resume (PDF or pasted text)
        │
        ▼
  pypdf text extraction            ─┐
  sha256 → resume_hash               │ Stage 0
        │                            │ (request shaping)
        ▼                           ─┘
  OpenAI embed (cache-aware)       ─┐
        │                            │ Stage 1: retrieval
        ▼                            │ (cheap, deterministic, fast)
  pgvector cosine  → top 10        ─┘
        │
        ▼                          ─┐
  fan-out 10× async tasks            │
  asyncio.Semaphore(5)               │ Stage 2: reasoning
  OpenAI gpt-5.4-mini per job        │ (expensive, parallel)
  (cache-aware, structured output) ─┘
        │
        ▼
  sort by score → top 5 → done
```

Every transition above also emits an SSE event so the UI fills in
progressively (`shortlist` → 10 × `match` → `done`).

### Two-stage retrieval (and why it generalizes)

At N=25 jobs we could brute-force every job through the LLM — about $0.001 per
match. At N=20,000 that becomes $0.80 per match in OpenAI tokens alone, and
~3 minutes of latency. The two-stage pattern is the same code at both scales:

1. **Stage 1 — vector shortlist.** Embed the resume once, run a single SQL
   query (`embedding <=> :vec`) to pull the top 10 candidates by cosine
   distance. Cheap, deterministic, single round-trip. At 25 rows it's a
   seq-scan; at 20k it becomes an HNSW or IVF-Flat index lookup — one DDL
   change, no pipeline change.
2. **Stage 2 — Claude-grade reasoning, in parallel.** Fan 10 OpenAI calls out
   under an `asyncio.Semaphore(5)` so we don't melt the rate limit. Use
   `asyncio.as_completed` (not `gather`) so each result hits the SSE queue as
   it lands. Sort by score, take top 5, emit `done`.

This is the standard "retrieve → rerank" pattern from search ranking, mapped
onto LLM reasoning. It keeps the expensive token spend proportional to a fixed
shortlist size regardless of corpus size.

### Why OpenAI (single vendor, both stages)

One SDK, one credential, one billing surface. Embeddings via
`text-embedding-3-small` (1536-dim, cheap, well-clustered). Reasoning via
`gpt-5.4-mini` — strong fit-evaluation quality per dollar, and the
mini size is the right call when you're firing 10 parallel calls per match
flow. Structured outputs via `client.chat.completions.parse(..., response_format=_LLMOutput)`
give a typed Pydantic instance at decode time: no JSON-parser, no retry-on-malformed,
no `json.loads(re.search(r'\{.*\}', ...))` hellscape.

The reasoning layer is provider-agnostic at the
[services/llm.py](backend/app/services/llm.py) boundary — swapping to
Anthropic, Gemini, or a local model is a single-file change. The orchestrator
only cares about the `JobMatch` schema coming back.

**Design refinement (Phase 3).** The locked `JobMatch` contract has six
fields (`job_id`, `title`, `company`, `score`, `reasoning`, `highlight_bullet`).
The LLM only produces the three creative ones (`score`, `reasoning`,
`highlight_bullet`) via a smaller internal `_LLMOutput` schema; the server
fills in `job_id` / `title` / `company` from the DB row it already has. This
saves tokens on every call and removes a class of hallucinations the model
would otherwise be free to commit (wrong id, drifted company name). The locked
SSE contract is unchanged.

### Why no LangChain / LangGraph

The brief explicitly invites skipping the agent-framework layer. The whole
orchestrator is **retrieve → fan-out → reduce** — about 30 lines of
`asyncio` in [api/match.py](backend/app/api/match.py). LangGraph would add a
dependency, a state-machine abstraction, and a debugging surface for a flow
that doesn't loop, doesn't branch, and doesn't need a tool-call planner.
"You can use plain async" is a valid answer to the prompt.

### Why SSE over WebSocket

This is one-way push (server → browser). HTTP-native, no upgrade handshake,
proxy-friendly, and `fetch` + `ReadableStream` parses it client-side without an
EventSource (which we couldn't use anyway — `EventSource` doesn't support
`POST` or `multipart/form-data`). WebSocket would buy us a reverse channel we
don't need.

### Two cache layers

| Layer | Key | TTL | What it saves |
|---|---|---|---|
| Embeddings | `embed:{sha256(text)}` | 7 days | One OpenAI embed call per unique resume text **and** per unique job text. Job embeddings are cached during seed; re-seeding the same `jobs.json` is free. |
| Match results | `match:{resume_hash}:{job_id}` | 24 hours | The expensive bit: one full `gpt-5.4-mini` reasoning call per (resume, job) pair. A cached re-run skips all 10 calls. |

**Headline number:** a fresh match takes ~7.8 seconds (10 parallel OpenAI
reasoning calls bottlenecked at concurrency=5). A cached re-run of the same
resume returns in ~52 ms. That's a **~150× speedup** and is the single most
visible win from running this end-to-end in the browser.

### Sentry transaction design

Each `/api/match` invocation produces exactly one Sentry transaction named
`match_resume_to_jobs` (op `match`), with 10 child spans
(op `llm.match_job`, description `job_id=N`) so per-call latency is visible in
the waterfall. `traces_sample_rate=1.0` for the demo; in production this
should be `~0.1` plus tail-based sampling of error transactions.

The transaction carries these tags at finish — designed so a single
transaction tells you everything you need to know about that flow without
clicking into the spans:

| Tag | Why it's here |
|---|---|
| `shortlist_size` | Sanity-check that Stage 1 returned what we expected (always 10 in this build). |
| `cache_hits` | Counts how many of the 10 jobs hit the result cache. The headline cache-efficacy number. |
| `cache_misses` | Inverse of above; together they let you compute cache ratio without doing arithmetic. |
| `total_tokens_in` | OpenAI prompt-token spend across all 10 reasoning calls in this flow. |
| `total_tokens_out` | OpenAI completion-token spend. The reasoning + bullet text. |
| `estimated_usd` | `(in × $0.75 + out × $4.50) / 1M`. A real dollar number per match flow — the most useful tag for budget conversations. |
| `resume_hash_prefix` | First 8 hex chars of the resume hash. Lets you correlate flows for the same resume without storing the resume itself. |
| `reasoning_model` | The exact model id from config. Makes it trivial to filter the dashboard when you swap models or A/B test. |

Per-job spans also carry a `cache=hit|miss` tag and (on success) a `score=N`
tag, so you can drill from "cheap flow" → "which jobs were the cache hits" in
two clicks.

### Locked contracts

[schemas.py](backend/app/schemas.py) was written first and not touched after:
`JobMatch` (the per-match payload), `ShortlistItem` (Stage 1 result),
`SSEEvent` (the discriminated union on the wire). The matcher system prompt
in [services/llm.py](backend/app/services/llm.py) is also a locked contract —
prompt edits during a build cause silent score drift. Both are pinned and
documented to be left alone outside of explicit prompt-versioning work.

---

## What I'd do differently with one more week

- **HNSW or IVF-Flat index** on the `embedding` column. At 25 rows pgvector
  seq-scans in microseconds, but the index is the production-ready answer.
- **Hybrid retrieval.** BM25 over `title || description` plus the vector
  shortlist, fused via reciprocal rank fusion. Vector embeddings miss
  literal-keyword matches (a candidate searching for "Kafka" should not lose
  to "event-driven streaming platforms").
- **Eval harness.** Gold-standard (resume, job, expected score band) tuples;
  measure rank correlation between human judgments and model scores per
  release. Without this, "prompt tweaks" are vibes.
- **Prompt versioning + A/B testing.** The matcher prompt is a load-bearing
  string. It deserves a version field, a registry, and a way to run two
  prompts side-by-side on the same shortlist.
- **Per-tenant cache namespacing.** `match:{tenant}:{resume_hash}:{job_id}`
  the day a second customer joins.
- **Try `gpt-5.5` for reasoning** when quality matters more than speed/cost,
  benchmark vs. mini on the eval harness, decide by p50 fit-score correlation
  and dollars per match.
- **Stream OpenAI's token output** as a separate SSE event per match so the
  reasoning paragraph types in as the model produces it. Better demo,
  marginal real value, hence not built.
- **Rate limiting + retry/backoff** on OpenAI 429s. Currently a 429 surfaces
  as a per-job `error` event and the flow continues; the right answer is
  exponential backoff with jitter.
- **Layout-aware PDF parser** (Unstructured.io, LlamaParse). pypdf is fine
  for single-column resumes, brittle for two-column / table-heavy ones.
- **`text-embedding-3-large`** if shortlist precision turns out to matter
  more than cost — at 20k jobs it pays for itself by promoting fewer false
  positives into the expensive Stage 2.

---

## Known issues / limitations / chose-not-to-fix

- pypdf doesn't OCR; **scanned/image-based PDFs** return empty text and the
  request 400s.
- **No rate limiting** on `/api/match`. A demo, not a production endpoint.
- **No retry/backoff** on upstream OpenAI errors. They surface to the client
  as per-job `error` events.
- **Models are not per-request configurable.** `OPENAI_REASONING_MODEL` is a
  process-level env var.
- **`jobs.json` is loaded only via the seed script.** No admin UI, no
  refresh endpoint.
- **24-hour result-cache TTL is arbitrary.** Resume content rarely changes,
  but model versions do; the right number is a function of model-release
  cadence.
- **DB schema via `Base.metadata.create_all` on startup.** Acceptable for a
  demo, not for prod — production deserves Alembic.

### Lessons learned (worth documenting)

- **sse-starlette emits `\r\n\r\n` between events** — canonical SSE — but a
  naive parser that splits on `\n\n` silently matches nothing and never
  fires any `onEvent`. The frontend SSE parser in
  [frontend/src/lib/sse.ts](frontend/src/lib/sse.ts) splits on `/\r?\n\r?\n/`
  and also drains buffer + flushes the decoder after `done`, so trailing
  events aren't dropped on stream close. Worth two minutes' staring at hex
  bytes if you ever see "DevTools shows N chunks but UI renders zero cards."

---

## File map

```
backend/
  app/
    api/match.py            POST /api/match — SSE producer + orchestrator
    db/
      models.py             Job (pgvector Vector(1536))
      session.py            async engine
    services/
      pdf.py                pypdf text extraction
      embeddings.py         OpenAI embed + Redis cache
      llm.py                OpenAI reasoning + structured output + locked prompt
      cache.py              match result cache helpers
      matcher.py            pgvector shortlist
    main.py                 FastAPI, Sentry init, CORS, lifespan
    config.py               pydantic-settings
    schemas.py              locked Pydantic contracts
    scripts/seed_jobs.py    one-shot: embed + upsert jobs.json
frontend/
  src/
    types.ts                TS mirrors of locked contracts
    lib/sse.ts              fetch + ReadableStream SSE consumer
    components/
      ResumeInput.tsx       upload/paste tabs
      MatchCard.tsx         score badge + reasoning + verbatim bullet
    App.tsx                 phase machine (idle → running → done)
docker-compose.yml          db, redis, backend, frontend
init.sql                    CREATE EXTENSION vector
jobs.json                   25 sample jobs (given)
BUILD_PLAN_updated.md       Phased build spec (v3)
```
