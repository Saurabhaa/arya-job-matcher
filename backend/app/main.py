from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.match import router as match_router
from app.config import settings
from app.db.models import Base
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema bootstrap: fine for a demo. Phase 5 README documents that
    # production would use Alembic instead.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Arya Job Matcher", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(match_router)
