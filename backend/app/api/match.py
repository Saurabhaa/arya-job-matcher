import asyncio
import hashlib
import json
import logging
from typing import Any

import sentry_sdk
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.schemas import JobMatch, ShortlistItem
from app.services.cache import get_match_cache, set_match_cache
from app.services.llm import LLMRefusal, TokenUsage, match_job
from app.services.matcher import shortlist
from app.services.pdf import extract_text

router = APIRouter(prefix="/api", tags=["match"])
log = logging.getLogger("arya.match")

# gpt-5.4-mini pricing (USD per 1M tokens), used to populate the
# estimated_usd Sentry tag. Verified against OpenAI's pricing page.
PRICE_PER_M_IN = 0.75
PRICE_PER_M_OUT = 4.50


def _event(type_: str, data: Any) -> dict[str, str]:
    return {"data": json.dumps({"type": type_, "data": data})}


async def _resume_text_from_request(
    file: UploadFile | None,
    resume_text: str | None,
) -> str:
    if file is not None:
        ctype = (file.content_type or "").lower()
        name = (file.filename or "").lower()
        if "pdf" not in ctype and not name.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="file must be a PDF")
        raw = await file.read()
        try:
            text = extract_text(raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"failed to parse PDF: {e}")
        if not text.strip():
            raise HTTPException(status_code=400, detail="no extractable text in PDF")
        return text

    if resume_text and resume_text.strip():
        return resume_text.strip()

    raise HTTPException(
        status_code=400,
        detail="provide either a PDF `file` or `resume_text` form field",
    )


async def _match_one(
    job: Job,
    resume_text: str,
    resume_hash: str,
    sem: asyncio.Semaphore,
    txn: Any,
) -> tuple[Job, JobMatch | Exception, bool, TokenUsage | None]:
    span = txn.start_child(op="llm.match_job", description=f"job_id={job.id}")
    try:
        cached = await get_match_cache(resume_hash, job.id)
        if cached is not None:
            span.set_tag("cache", "hit")
            log.info("match cache hit resume=%s job=%d", resume_hash[:8], job.id)
            return job, cached, True, None

        span.set_tag("cache", "miss")
        log.info("match cache miss resume=%s job=%d", resume_hash[:8], job.id)
        async with sem:
            try:
                result, usage = await match_job(resume_text, job)
            except (LLMRefusal, Exception) as e:
                span.set_status("internal_error")
                return job, e, False, None
        await set_match_cache(resume_hash, job.id, result)
        span.set_tag("score", result.score)
        return job, result, False, usage
    finally:
        span.finish()


async def _produce_events(
    resume_text: str,
    resume_hash: str,
    queue: asyncio.Queue,
) -> None:
    with sentry_sdk.start_transaction(
        op="match", name="match_resume_to_jobs"
    ) as txn:
        cache_hits = 0
        cache_misses = 0
        total_tokens_in = 0
        total_tokens_out = 0
        shortlist_size = 0

        try:
            # Stage 1: shortlist
            async with SessionLocal() as session:
                pairs = await shortlist(
                    resume_text, session, k=settings.SHORTLIST_SIZE
                )

            shortlist_size = len(pairs)
            items = [
                ShortlistItem(
                    job_id=job.id,
                    title=job.title,
                    company=job.company,
                    distance=dist,
                ).model_dump()
                for job, dist in pairs
            ]
            log.info(
                "shortlist ready: resume=%s items=%d",
                resume_hash[:8],
                len(items),
            )
            await queue.put(_event("shortlist", items))

            # Stage 2: parallel LLM + per-job spans
            sem = asyncio.Semaphore(settings.LLM_CONCURRENCY)
            tasks = [
                asyncio.create_task(
                    _match_one(job, resume_text, resume_hash, sem, txn)
                )
                for job, _ in pairs
            ]

            matches: list[JobMatch] = []
            for fut in asyncio.as_completed(tasks):
                job, result, hit, usage = await fut
                if hit:
                    cache_hits += 1
                else:
                    cache_misses += 1
                if isinstance(result, Exception):
                    log.exception(
                        "match failed job=%d", job.id, exc_info=result
                    )
                    sentry_sdk.capture_exception(result)
                    await queue.put(
                        _event(
                            "error",
                            {
                                "job_id": job.id,
                                "message": str(result) or type(result).__name__,
                            },
                        )
                    )
                    continue
                if usage is not None:
                    total_tokens_in += usage.prompt_tokens
                    total_tokens_out += usage.completion_tokens
                matches.append(result)
                await queue.put(_event("match", result.model_dump()))

            top5 = sorted(matches, key=lambda m: m.score, reverse=True)[:5]
            estimated_usd = round(
                (total_tokens_in * PRICE_PER_M_IN + total_tokens_out * PRICE_PER_M_OUT)
                / 1_000_000,
                6,
            )
            stats = {
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
            }
            tags = {
                "shortlist_size": shortlist_size,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "estimated_usd": estimated_usd,
                "resume_hash_prefix": resume_hash[:8],
                "reasoning_model": settings.OPENAI_REASONING_MODEL,
            }
            for k, v in tags.items():
                txn.set_tag(k, v)
            # Also log the tag set so it's recoverable from stdout when no
            # live Sentry project is connected.
            log.info("sentry txn tags=%s", json.dumps(tags))

            await queue.put(
                _event(
                    "done",
                    {"top5": [m.model_dump() for m in top5], "stats": stats},
                )
            )
        except Exception as e:
            log.exception("producer error")
            sentry_sdk.capture_exception(e)
            await queue.put(_event("error", {"message": str(e)}))
        finally:
            await queue.put(None)


@router.post("/match")
async def match(
    file: UploadFile | None = File(default=None),
    resume_text: str | None = Form(default=None),
) -> EventSourceResponse:
    text = await _resume_text_from_request(file, resume_text)
    resume_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    log.info("match start: resume=%s len=%d", resume_hash[:8], len(text))

    queue: asyncio.Queue = asyncio.Queue()
    producer = asyncio.create_task(_produce_events(text, resume_hash, queue))

    async def event_stream():
        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                yield msg
        finally:
            if not producer.done():
                producer.cancel()

    return EventSourceResponse(event_stream(), media_type="text/event-stream")
