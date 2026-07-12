import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, Date, DateTime, ForeignKey, JSON, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class HCP(Base):
    """A Healthcare Professional (doctor, prescriber) tracked by the field rep."""
    __tablename__ = "hcps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    specialty: Mapped[str] = mapped_column(String(100), nullable=True)
    institution: Mapped[str] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(150), nullable=True)
    phone: Mapped[str] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=True)  # used for compliance tool
    tier: Mapped[str] = mapped_column(String(20), default="B")  # A/B/C prescriber tier
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    interactions: Mapped[list["Interaction"]] = relationship(back_populates="hcp", cascade="all, delete-orphan")


class Interaction(Base):
    """A single logged HCP interaction (visit, call, email, sample drop, etc.)."""
    __tablename__ = "interactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    hcp_id: Mapped[str] = mapped_column(String(36), ForeignKey("hcps.id"), nullable=False)

    interaction_type: Mapped[str] = mapped_column(String(50), default="Visit")  # Visit/Call/Email/Sample Drop/Conference
    channel: Mapped[str] = mapped_column(String(20), default="form")  # "form" or "chat"
    interaction_date: Mapped[date] = mapped_column(Date, default=date.today)

    products_discussed: Mapped[list] = mapped_column(JSON, default=list)
    samples_dropped: Mapped[list] = mapped_column(JSON, default=list)  # [{"product": "..", "qty": n}]
    materials_shared: Mapped[list] = mapped_column(JSON, default=list)
    key_topics: Mapped[list] = mapped_column(JSON, default=list)

    hcp_sentiment: Mapped[str] = mapped_column(String(20), nullable=True)  # Positive/Neutral/Negative
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=True)

    raw_notes: Mapped[str] = mapped_column(Text, nullable=True)  # original free-text / chat transcript
    ai_summary: Mapped[str] = mapped_column(Text, nullable=True)  # LLM-generated summary
    next_best_action: Mapped[str] = mapped_column(Text, nullable=True)

    compliance_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    compliance_notes: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    edit_history: Mapped[list] = mapped_column(JSON, default=list)  # audit trail of edits

    hcp: Mapped["HCP"] = relationship(back_populates="interactions")


class FollowUp(Base):
    """A follow-up task/reminder created by the agent's scheduling tool."""
    __tablename__ = "follow_ups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    hcp_id: Mapped[str] = mapped_column(String(36), ForeignKey("hcps.id"), nullable=False)
    interaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("interactions.id"), nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="Pending")  # Pending/Done/Cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    """Persisted turn of a conversational Log Interaction chat session, for replay/debug."""
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/tool
    content: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
