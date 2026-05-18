from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.services.embeddings import embed


async def shortlist(
    resume_text: str,
    session: AsyncSession,
    k: int = 10,
) -> list[tuple[Job, float]]:
    """Embed the resume and return top-k jobs by cosine distance.

    Returns a list of (Job, distance) pairs, nearest first. Embedding is
    cache-aware via services.embeddings.embed.
    """
    vec = await embed(resume_text)

    # pgvector cosine distance via <=> operator.
    sql = text(
        """
        SELECT id, title, company, location, description,
               embedding <=> CAST(:vec AS vector) AS distance
        FROM jobs
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    result = await session.execute(sql, {"vec": str(vec), "k": k})
    rows = result.mappings().all()

    out: list[tuple[Job, float]] = []
    for r in rows:
        job = Job(
            id=r["id"],
            title=r["title"],
            company=r["company"],
            location=r["location"],
            description=r["description"],
        )
        out.append((job, float(r["distance"])))
    return out
