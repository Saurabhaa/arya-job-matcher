"""Redis helpers for the per-(resume, job) match cache.

Key shape: `match:{resume_hash}:{job_id}`. TTL from settings.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings
from app.schemas import JobMatch

_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _key(resume_hash: str, job_id: int) -> str:
    return f"match:{resume_hash}:{job_id}"


async def get_match_cache(resume_hash: str, job_id: int) -> JobMatch | None:
    raw = await _client().get(_key(resume_hash, job_id))
    if raw is None:
        return None
    return JobMatch.model_validate_json(raw)


async def set_match_cache(resume_hash: str, job_id: int, match: JobMatch) -> None:
    await _client().set(
        _key(resume_hash, job_id),
        match.model_dump_json(),
        ex=settings.RESULT_CACHE_TTL_SECONDS,
    )
