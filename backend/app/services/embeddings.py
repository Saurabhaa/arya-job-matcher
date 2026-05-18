import hashlib
import json
import logging

from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.config import settings

log = logging.getLogger("arya.embeddings")

EMBED_TTL_SECONDS = 7 * 24 * 3600

_openai: AsyncOpenAI | None = None
_redis: Redis | None = None


def _client() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai


def _cache() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _key(text: str) -> str:
    return f"embed:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


async def embed(text: str) -> list[float]:
    key = _key(text)
    cache = _cache()
    cached = await cache.get(key)
    if cached:
        log.info("embed cache hit key=%s", key)
        return json.loads(cached)

    log.info("embed cache miss key=%s", key)
    resp = await _client().embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=text,
    )
    vec = resp.data[0].embedding
    await cache.set(key, json.dumps(vec), ex=EMBED_TTL_SECONDS)
    return vec
