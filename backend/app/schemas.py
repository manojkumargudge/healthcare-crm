from datetime import date, datetime, time
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.catalog import Sentiment, clean_names
from app.samples import SampleDrop

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


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
    interaction_time: Optional[time] = None
    products_discussed: list[str] = []
    samples_dropped: list[SampleDrop] = []
    materials_shared: list[str] = []
    key_topics: list[str] = []
    attendees: list[Name] = Field(default=[], max_length=50)
    # Set by the rep in the form; the form path never calls the LLM, so the
    # stored label is exactly this and sentiment_score stays null.
    hcp_sentiment: Optional[Sentiment] = None
    raw_notes: Optional[str] = None
    outcomes: Optional[str] = None
    follow_up_actions: Optional[str] = None
    # Set when a voice note was recorded: consent_at comes from the voice-note endpoint.
    voice_note_consent: bool = False
    voice_note_consent_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _consent_timestamp_needs_consent(self) -> "InteractionCreate":
        if self.voice_note_consent_at is not None and not self.voice_note_consent:
            raise ValueError("voice_note_consent_at given without voice_note_consent=true")
        return self

    @field_validator("attendees")
    @classmethod
    def _dedupe_attendees(cls, v: list[str]) -> list[str]:
        return clean_names(v)


class InteractionUpdate(BaseModel):
    interaction_type: Optional[str] = None
    products_discussed: Optional[list[str]] = None
    samples_dropped: Optional[list[SampleDrop]] = None
    materials_shared: Optional[list[str]] = None
    key_topics: Optional[list[str]] = None
    attendees: Optional[list[Name]] = Field(default=None, max_length=50)
    raw_notes: Optional[str] = None
    outcomes: Optional[str] = None
    follow_up_actions: Optional[str] = None
    hcp_sentiment: Optional[Sentiment] = None  # manual label: clears the LLM sentiment_score
    edited_by: Optional[str] = "field_rep"
    edit_reason: Optional[str] = None


class InteractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    hcp_id: str
    interaction_type: str
    channel: str
    interaction_date: date
    interaction_time: Optional[time] = None
    products_discussed: list
    samples_dropped: list
    materials_shared: list
    key_topics: list
    attendees: list = []
    hcp_sentiment: Optional[str] = None
    sentiment_score: Optional[float] = None
    raw_notes: Optional[str] = None
    ai_summary: Optional[str] = None
    next_best_action: Optional[str] = None
    outcomes: Optional[str] = None
    follow_up_actions: Optional[str] = None
    voice_note_consent: bool = False
    voice_note_consent_at: Optional[datetime] = None
    compliance_flag: bool
    compliance_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    edit_history: list


class MaterialCreate(BaseModel):
    name: Name


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    approved: bool


class ProductCreate(BaseModel):
    name: Name


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class VoiceNoteSummary(BaseModel):
    transcript: str
    summary: str
    consent_at: datetime
    transcription_model: str


class FollowUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    hcp_id: str
    interaction_id: Optional[str] = None
    due_date: date
    reason: str
    status: str
    created_at: datetime


class FollowUpStatusUpdate(BaseModel):
    status: Literal["Pending", "Dismissed", "Done", "Cancelled"]


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str
    hcp_id: Optional[str] = None  # optional pre-selected HCP context


class ChatTurnResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict] = []
    interaction: Optional[InteractionOut] = None
