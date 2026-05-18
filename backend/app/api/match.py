import asyncio
import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.db.session import SessionLocal
from app.schemas import ShortlistItem
from app.services.matcher import shortlist
from app.services.pdf import extract_text

router = APIRouter(prefix="/api", tags=["match"])
log = logging.getLogger("arya.match")


def _event(type_: str, data: Any) -> dict[str, str]:
    """Wrap a payload in the locked SSEEvent shape, JSON-encoded for SSE."""
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
        except Exception as e:  # pypdf raises a variety of errors on malformed PDFs
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


async def _produce_events(
    resume_text: str,
    resume_hash: str,
    queue: asyncio.Queue,
) -> None:
    """Stage-1 producer: shortlist over pgvector, push events to the queue."""
    try:
        async with SessionLocal() as session:
            pairs = await shortlist(resume_text, session, k=settings.SHORTLIST_SIZE)

        items = [
            ShortlistItem(
                job_id=job.id,
                title=job.title,
                company=job.company,
                distance=dist,
            ).model_dump()
            for job, dist in pairs
        ]
        log.info("shortlist ready: resume=%s items=%d", resume_hash[:8], len(items))
        await queue.put(_event("shortlist", items))

        # Phase 2: no reasoning yet. Stub a done event so the stream closes.
        await queue.put(_event("done", {}))
    except Exception as e:
        log.exception("producer error")
        await queue.put(_event("error", {"message": str(e)}))
    finally:
        await queue.put(None)  # sentinel


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
