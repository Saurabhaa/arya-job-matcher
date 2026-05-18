from pgvector.sqlalchemy import Vector
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    company: Mapped[str]
    location: Mapped[str | None] = mapped_column(nullable=True)
    description: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))

    __table_args__ = (UniqueConstraint("title", "company", name="uq_job"),)
