from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status", "status"), Index("ix_jobs_created_at", "created_at"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workflow: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    input_path: Mapped[str] = mapped_column(Text)
    output_directory: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    names_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    review_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stages: Mapped[list["JobStage"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    records: Mapped[list["JobRecord"]] = relationship(back_populates="job", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("status", "queued")
        kwargs.setdefault("configuration", {})
        kwargs.setdefault("total_rows", 0)
        kwargs.setdefault("names_found", 0)
        kwargs.setdefault("failed_rows", 0)
        kwargs.setdefault("review_rows", 0)
        super().__init__(**kwargs)


class JobStage(Base):
    __tablename__ = "job_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped[Job] = relationship(back_populates="stages")


class JobRecord(Base):
    __tablename__ = "job_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    tva: Mapped[str] = mapped_column(String(32), default="")
    outcome: Mapped[str] = mapped_column(String(32))
    review_state: Mapped[str] = mapped_column(String(32), default="none")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job: Mapped[Job] = relationship(back_populates="records")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_record_id: Mapped[int] = mapped_column(ForeignKey("job_records.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(16), default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    record: Mapped[JobRecord] = relationship(back_populates="evidence")
