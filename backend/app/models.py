from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Index,
    UniqueConstraint
)
from app.db import Base


class Professor(Base):
    __tablename__ = "professors"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    department = Column(String(255), nullable=True)
    aliases_json = Column(Text, default="[]")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, index=True)
    date = Column(DateTime, nullable=True, index=True)
    author_hash = Column(String(64), nullable=True, index=True)
    text_clean = Column(Text, default="")
    char_len = Column(Integer, default=0)
    reply_to_message_id = Column(Integer, nullable=True, index=True)
    thread_root_id = Column(Integer, nullable=True)
    processed_matching = Column(Boolean, default=False)
    processed_llm = Column(Boolean, default=False)
    llm_error_count = Column(Integer, default=0)
    has_embedding = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    reactions_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_messages_date_processed", "date", "processed_llm"),
    )


class MessageProfessor(Base):
    __tablename__ = "message_professors"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), index=True)
    professor_id = Column(Integer, ForeignKey("professors.id"), index=True)
    match_method = Column(String(32), default="manual")
    matched_alias = Column(String(255), nullable=True)
    confidence = Column(Float, default=0.0)
    needs_review = Column(Boolean, default=False)
    approved = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "professor_id",
            "match_method",
            name="uq_message_prof_method"
        ),
        Index("ix_mp_professor", "professor_id"),
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), index=True)
    professor_id = Column(Integer, ForeignKey("professors.id"), nullable=True, index=True)
    analyzer_name = Column(String(100), default="llm_basic")
    analyzer_version = Column(String(20), default="v1")
    model_name = Column(String(100), nullable=True)
    prompt_version = Column(String(20), default="v1")
    is_relevant = Column(Boolean, default=False)
    message_type = Column(String(30), nullable=True)
    sentiment = Column(String(20), nullable=True)
    sentiment_score = Column(Float, nullable=True)
    aspects_json = Column(Text, default="[]")
    topics_json = Column(Text, default="[]")
    severity = Column(String(20), default="none")
    confidence = Column(Float, default=0.0)
    raw_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    contains_opinion = Column(Boolean, default=True, nullable=False)
    key_point = Column(Text, default="")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    status = Column(String(20), default="pending")
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProfessorSummary(Base):
    __tablename__ = "professor_summaries"
    id = Column(Integer, primary_key=True, index=True)
    professor_id = Column(Integer, ForeignKey("professors.id"), unique=True, index=True)
    summary_text = Column(Text, default="")
    strengths_json = Column(Text, default="[]")
    weaknesses_json = Column(Text, default="[]")
    warnings_json = Column(Text, default="[]")
    model_name = Column(String(100), default="")
    generated_at = Column(DateTime)