"""OpenAI reasoning wrapper.

Uses the SDK's structured-output `parse` helper. The LLM emits a *creative*
subset (`score`, `reasoning`, `highlight_bullet`); the server fills in
`job_id`, `title`, `company` from the known DB row to assemble the final
`JobMatch`. This avoids forcing the model to hallucinate an id and keeps the
prompt short.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.config import settings
from app.db.models import Job
from app.schemas import JobMatch

MATCHER_SYSTEM_PROMPT = """\
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
"""


class _LLMOutput(BaseModel):
    """Subset the LLM produces; job_id/title/company are filled server-side."""
    score: int = Field(ge=0, le=100)
    reasoning: str
    highlight_bullet: str


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int


class LLMRefusal(Exception):
    pass


_client: AsyncOpenAI | None = None


def _openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _user_message(resume_text: str, job: Job) -> str:
    return (
        f"RESUME:\n{resume_text}\n\n"
        f"JOB_DESCRIPTION:\n{job.title} at {job.company}\n{job.description}"
    )


async def match_job(resume_text: str, job: Job) -> tuple[JobMatch, TokenUsage]:
    completion = await _openai().chat.completions.parse(
        model=settings.OPENAI_REASONING_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": MATCHER_SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(resume_text, job)},
        ],
        response_format=_LLMOutput,
    )
    msg = completion.choices[0].message
    if getattr(msg, "refusal", None):
        raise LLMRefusal(msg.refusal)
    parsed: _LLMOutput | None = msg.parsed
    if parsed is None:
        raise RuntimeError("OpenAI returned no parsed output")

    usage = completion.usage
    return (
        JobMatch(
            job_id=job.id,
            title=job.title,
            company=job.company,
            score=parsed.score,
            reasoning=parsed.reasoning,
            highlight_bullet=parsed.highlight_bullet,
        ),
        TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
    )
