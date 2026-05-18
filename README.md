# Arya Job Matcher

Upload or paste a resume. Get the top 5 matching jobs back with reasoning and one verbatim resume bullet to feature per match. Results stream in over SSE so you don't sit watching a spinner.

FastAPI + async SQLAlchemy + Postgres/pgvector + Redis + OpenAI (gpt-5.4-mini for reasoning, text-embedding-3-small for embeddings) + Sentry. React + Vite + Tailwind on the front. docker-compose ties it together.

## Setup

Five commands from a clone:

```bash
git clone <repo> arya && cd arya
cp .env.example .env          # fill OPENAI_API_KEY (required), SENTRY_DSN (optional)
docker compose up -d --build
docker compose exec backend python -m app.scripts.seed_jobs
open http://localhost:5173
```

Health check: `curl http://localhost:8001/health`.

One thing about the port. 8000 was taken on my dev machine, so docker-compose maps the backend as `8001:8000` — host 8001, container still 8000. If 8000 is free for you, change the backend service's `ports` in [docker-compose.yml](docker-compose.yml) to `"8000:8000"` and flip the `API_URL` constant in [frontend/src/lib/sse.ts](frontend/src/lib/sse.ts).

## Architecture

This is the section the brief weights heaviest, so I'll spend the most time on it.

The pipeline is two stages plus an SSE wrapper. Stage 1 embeds the resume and runs a single pgvector cosine query to pull the top 10 candidates by distance. Stage 2 fans out 10 OpenAI reasoning calls in parallel under a semaphore, collects results as they finish, sorts by score, and emits the top 5. Every transition emits an SSE event so the UI fills in progressively instead of blocking on the final answer.

The reason to do it in two stages is cost. At 25 jobs you could brute-force every job through the LLM for about $0.001 per match. At 20,000 jobs the same flow runs you about $0.80 per match and takes three minutes. Retrieve-then-rerank is the standard search-ranking pattern; it keeps the expensive token spend proportional to a fixed shortlist size regardless of corpus size. At 20k rows you swap the seq-scan for an HNSW index — one DDL change, no pipeline change.

I picked OpenAI for both stages because single-vendor means one SDK, one credential, one billing surface. Embeddings via text-embedding-3-small, reasoning via gpt-5.4-mini. Mini is the right size when you're firing 10 parallel calls per match — bigger models would be overkill and slower. Structured output via `client.chat.completions.parse(response_format=_LLMOutput)` gives a typed Pydantic instance at decode time, which kills the parser-and-retry-on-malformed-JSON dance. The reasoning layer is provider-agnostic at the [services/llm.py](backend/app/services/llm.py) boundary; swapping to Anthropic or Gemini is a one-file change.

One design refinement worth calling out. The locked `JobMatch` contract has six fields. The LLM only produces three of them — score, reasoning, highlight_bullet — via a smaller internal schema. The server fills in job_id, title, and company from the DB row it already has. This saves prompt tokens on every call and removes a class of hallucination (drifted company name, wrong id) the model would otherwise be free to commit. The SSE contract over the wire still ships the full `JobMatch`.

I skipped LangChain and LangGraph. The brief invited it. The orchestrator is retrieve, fan out, reduce — about 30 lines of asyncio in [api/match.py](backend/app/api/match.py). There's no loop, no branch, no tool-call planner. LangGraph would add a state-machine abstraction and a debugging surface for a flow that doesn't need either.

SSE over WebSocket because the channel is one-way (server to browser), HTTP-native, and proxy-friendly. You can't use a stock `EventSource` here because it doesn't support POST or multipart, so the frontend reads the response body with `fetch` + `getReader()` and parses the SSE format by hand.

There are two cache layers, and they pull most of the weight in this system:

| Layer | Key | TTL | What it saves |
|---|---|---|---|
| Embeddings | embed:{sha256(text)} | 7d | One OpenAI embed call per unique resume text, and per unique job text on seed |
| Match results | match:{resume_hash}:{job_id} | 24h | The expensive bit — one full gpt-5.4-mini call per (resume, job) pair |

A fresh match takes about 7.8 seconds end to end, bottlenecked by 10 OpenAI calls at concurrency 5. A cached re-run of the same resume returns in about 52 ms. That's roughly 150× and it's the single most visible win from the cache layer.

Observability runs through one Sentry transaction per match flow, named `match_resume_to_jobs`, op `match`. Each of the 10 LLM calls is a child span (op `llm.match_job`, description `job_id=N`), so per-call latency is visible in the waterfall. `traces_sample_rate=1.0` for this demo — in production it would be closer to 0.1 with tail-based sampling of error transactions. The transaction carries eight tags so a single record tells you everything about the flow:

| Tag | Why |
|---|---|
| shortlist_size | Sanity-check that Stage 1 returned what we expected |
| cache_hits | The headline cache-efficacy number |
| cache_misses | Lets you compute the ratio without arithmetic |
| total_tokens_in | Prompt-token spend across the 10 calls |
| total_tokens_out | Completion-token spend |
| estimated_usd | (in × $0.75 + out × $4.50) / 1M — a real dollar per flow |
| resume_hash_prefix | First 8 hex chars; lets you correlate without storing the resume |
| reasoning_model | The exact model id; useful when A/B-ing models |

Per-job spans also carry `cache=hit|miss` and (on success) `score=N`, so you can drill from "cheap flow" to "which jobs were the cache hits" in a couple of clicks.

The Pydantic models in [backend/app/schemas.py](backend/app/schemas.py) and the matcher system prompt in [services/llm.py](backend/app/services/llm.py) were written once and not edited mid-build. Prompt edits cause silent score drift; contract edits cause silent serialization bugs in the frontend. Both stay pinned.

## What I'd do with another week

- HNSW or IVF-Flat index on the embedding column. At 25 rows it doesn't matter; at 20k it does.
- Hybrid BM25 + vector retrieval, fused with reciprocal rank fusion. Vectors miss literal keywords — a candidate searching "Kafka" shouldn't lose to "event-driven streaming platforms".
- Eval harness — gold-standard (resume, job, expected score band) tuples and rank-correlation tracking across releases. Without this, prompt tweaks are vibes.
- Prompt versioning and A/B infra. The matcher prompt is a load-bearing string and deserves a version field.
- Per-tenant cache namespacing the moment a second customer joins.
- Benchmark gpt-5.5 for reasoning when quality matters more than cost.
- Stream OpenAI's token output as a separate SSE event per match so the reasoning paragraph types in. Better demo, marginal real value.
- Retry/backoff on OpenAI 429s. Right now a 429 surfaces as a per-job error event.
- Layout-aware PDF parser (Unstructured, LlamaParse). pypdf is brittle on two-column resumes.
- text-embedding-3-large if shortlist precision starts to matter more than cost.

## Known issues

pypdf doesn't OCR, so scanned PDFs return empty text and the request 400s.

No rate limiting on `/api/match`. No retry/backoff on upstream OpenAI errors — they bubble out as per-job `error` events on the SSE stream. Models aren't per-request configurable; the model id is a process-level env var. `jobs.json` loads via the seed script only — no admin UI. The 24-hour result-cache TTL is arbitrary; the right number is a function of model-release cadence more than anything else. The DB schema gets created via `Base.metadata.create_all` on startup, which is fine for a demo and not fine for production — Alembic is the right answer there.

One thing worth writing down for the next person. sse-starlette emits canonical SSE with `\r\n\r\n` between events. A naive frontend parser that splits on `\n\n` will silently match nothing and never fire `onEvent`, which presents in DevTools as "stream looks fine, UI renders zero cards." The parser in [frontend/src/lib/sse.ts](frontend/src/lib/sse.ts) splits on `/\r?\n\r?\n/` and drains the buffer after the stream closes so trailing events aren't dropped. I lost an hour to that.
