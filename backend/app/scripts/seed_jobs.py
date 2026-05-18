"""One-shot seed: load jobs.json, embed each, upsert by (title, company).

Idempotent. Source string IDs (e.g. "job_001") are ignored — DB autoincrements.
Re-running updates description/location/embedding for existing (title, company).

Usage (inside the backend container):
    python -m app.scripts.seed_jobs
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Base, Job
from app.db.session import SessionLocal, engine
from app.services.embeddings import embed

JOBS_PATH = Path("/app/jobs.json")


async def _ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def main() -> None:
    await _ensure_schema()

    raw = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = raw["jobs"] if isinstance(raw, dict) and "jobs" in raw else raw

    print(f"[seed] loaded {len(jobs)} jobs from {JOBS_PATH}")

    async with SessionLocal() as session:
        for j in jobs:
            title = j["title"]
            company = j["company"]
            description = j["description"]
            location = j.get("location")

            text = f"{title}\n\n{description}"
            vec = await embed(text)

            stmt = pg_insert(Job).values(
                title=title,
                company=company,
                location=location,
                description=description,
                embedding=vec,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_job",
                set_={
                    "location": stmt.excluded.location,
                    "description": stmt.excluded.description,
                    "embedding": stmt.excluded.embedding,
                },
            )
            await session.execute(stmt)
            print(f"[seed] upserted: {company} — {title}")

        await session.commit()

    print("[seed] done.")


if __name__ == "__main__":
    asyncio.run(main())
