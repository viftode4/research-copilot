"""SQLAlchemy models for the experiment knowledge base."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Experiment(Base):
    """An ML experiment with its configuration, results, and metadata."""

    __tablename__ = "experiments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    hypothesis = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(
        String(50), nullable=False, default="planned"
    )  # planned, running, completed, failed, cancelled
    config = Column(JSONB, nullable=True)  # hyperparams, model config, etc.
    results = Column(JSONB, nullable=True)  # metrics, scores, etc.
    wandb_run_id = Column(String(255), nullable=True)
    wandb_run_url = Column(Text, nullable=True)
    slurm_job_id = Column(String(100), nullable=True)
    script_path = Column(Text, nullable=True)
    dataset = Column(String(255), nullable=True)
    model_type = Column(String(255), nullable=True)
    tags = Column(JSONB, nullable=True, default=list)
    parent_experiment_id = Column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=True
    )
    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    insights = relationship("Insight", back_populates="experiment", lazy="selectin")
    parent = relationship("Experiment", remote_side="Experiment.id", lazy="selectin")

    __table_args__ = (
        Index("ix_experiments_status", "status"),
        Index("ix_experiments_dataset", "dataset"),
        Index("ix_experiments_model_type", "model_type"),
        Index("ix_experiments_created_at", "created_at"),
        Index("ix_experiments_tags", "tags", postgresql_using="gin"),
    )


class Insight(Base):
    """A research insight or lesson learned from an experiment."""

    __tablename__ = "insights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_id = Column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=True
    )
    category = Column(
        String(100), nullable=False
    )  # finding, failure, hypothesis, technique, observation
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)  # 0.0 to 1.0
    tags = Column(JSONB, nullable=True, default=list)
    metadata = Column(JSONB, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    experiment = relationship("Experiment", back_populates="insights")

    __table_args__ = (
        Index("ix_insights_category", "category"),
        Index("ix_insights_tags", "tags", postgresql_using="gin"),
        Index("ix_insights_created_at", "created_at"),
    )


class Paper(Base):
    """A research paper tracked in the knowledge base."""

    __tablename__ = "papers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(Text, nullable=False)
    authors = Column(JSONB, nullable=False, default=list)  # list of author names
    abstract = Column(Text, nullable=True)
    arxiv_id = Column(String(50), nullable=True, unique=True)
    semantic_scholar_id = Column(String(100), nullable=True, unique=True)
    doi = Column(String(255), nullable=True)
    url = Column(Text, nullable=True)
    venue = Column(String(500), nullable=True)
    year = Column(Integer, nullable=True)
    citation_count = Column(Integer, nullable=True)
    relevance_notes = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=True, default=list)
    added_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_papers_arxiv_id", "arxiv_id"),
        Index("ix_papers_year", "year"),
        Index("ix_papers_tags", "tags", postgresql_using="gin"),
    )


class ResearchContext(Base):
    """Persistent research context — goals, hypotheses, ongoing threads."""

    __tablename__ = "research_context"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    key = Column(String(255), nullable=False, unique=True)  # e.g., "current_goal", "lcdb_v2_plan"
    value = Column(Text, nullable=False)
    context_type = Column(
        String(100), nullable=False
    )  # goal, plan, note, reference, constraint
    metadata = Column(JSONB, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by = Column(String(255), nullable=True)

    __table_args__ = (Index("ix_research_context_type", "context_type"),)
