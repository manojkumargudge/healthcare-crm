from datetime import date, datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict


class HCPBase(BaseModel):
    name: str
    specialty: Optional[str] = None
    institution: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    state: Optional[str] = None
    tier: Optional[str] = "B"


class HCPCreate(HCPBase):
    pass


class HCPOut(HCPBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime


class InteractionCreate(BaseModel):
    hcp_id: str
    interaction_type: str = "Visit"
    channel: str = "form"
    interaction_date: Optional[date] = None
    products_discussed: list[str] = []
    samples_dropped: list[dict] = []
    materials_shared: list[str] = []
    key_topics: list[str] = []
    raw_notes: Optional[str] = None


class InteractionUpdate(BaseModel):
    interaction_type: Optional[str] = None
    products_discussed: Optional[list[str]] = None
    samples_dropped: Optional[list[dict]] = None
    materials_shared: Optional[list[str]] = None
    key_topics: Optional[list[str]] = None
    raw_notes: Optional[str] = None
    hcp_sentiment: Optional[str] = None
    edited_by: Optional[str] = "field_rep"
    edit_reason: Optional[str] = None


class InteractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    hcp_id: str
    interaction_type: str
    channel: str
    interaction_date: date
    products_discussed: list
    samples_dropped: list
    materials_shared: list
    key_topics: list
    hcp_sentiment: Optional[str] = None
    sentiment_score: Optional[float] = None
    raw_notes: Optional[str] = None
    ai_summary: Optional[str] = None
    next_best_action: Optional[str] = None
    compliance_flag: bool
    compliance_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    edit_history: list


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str
    hcp_id: Optional[str] = None  # optional pre-selected HCP context


class ChatTurnResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict] = []
    interaction: Optional[InteractionOut] = None
