# Arya Job Matcher

Resume → top 5 matching jobs with personalized reasoning, streamed progressively.
Two-stage retrieval (pgvector shortlist → Claude reasoning), Redis caching, Sentry observability.

> **Status:** Phase 1 complete. See `BUILD_PLAN.md` for the full phased build.

---

## Setup (current — Phase 1)

```bash
cp .env.example .env
# fill ANTHROPIC_API_KEY and OPENAI_API_KEY (SENTRY_DSN optional until Phase 4)

docker compose up -d --build

# seed 20+ jobs with embeddings (one-shot, idempotent)
docker compose exec backend python -m app.scripts.seed_jobs
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

# open http://localhost:5173 — placeholder page, full UI lands in Phase 4
```

Inspect the seeded DB:

```bash
docker compose exec db psql -U arya -d arya -c \
  "SELECT count(*) FROM jobs WHERE embedding IS NOT NULL;"
```

---

## Scope calls

- Source `jobs.json` uses string IDs (`"job_001"`). The locked `Job` model uses
  an int PK and unique `(title, company)`. The source string id is **not**
  persisted; the DB autoincrements and uniqueness is enforced on the
  `(title, company)` pair. Re-running the seed updates existing rows.
- No Alembic. Schema is created via `Base.metadata.create_all` on startup
  (and at the top of the seed script). Documented as a non-prod choice; Phase 5
  README will expand on the trade-off.

---

## Architecture, design notes, what-I'd-do-with-a-week, and known issues

Land in Phase 5. See `BUILD_PLAN.md` for the planned content.
