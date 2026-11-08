"""Voice note -> transcript -> "Topics Discussed" summary.

Privacy rules (enforced here, tested in tests/test_voice_notes.py):
- Nothing is accepted without the HCP's recorded consent.
- Audio is never stored: the request body is read into memory (no multipart
  parsing, so no spooled temp files), sent to Groq for transcription, and
  dropped. Neither audio nor transcript is written to the database or logs.
- Size and type are limited before anything is sent to Groq.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.agent.llm import ainvoke_resilient, get_async_groq
from app.config import settings
from app.models import utcnow

logger = logging.getLogger("hcp_crm.voice_notes")

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # Groq accepts more; a few minutes of compressed speech is far smaller
MAX_SUMMARY_WORDS = 120

# content type -> (file extension for Groq, magic-byte check)
_SIGNATURES = {
    "webm": lambda b: b[:4] == b"\x1a\x45\xdf\xa3",
    "ogg": lambda b: b[:4] == b"OggS",
    "wav": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WAVE",
    "mp3": lambda b: b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0),
    "m4a": lambda b: b[4:8] == b"ftyp",
    "flac": lambda b: b[:4] == b"fLaC",
}
ALLOWED_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/flac": "flac",
}


class VoiceNoteError(Exception):
    """Client-visible failure with an HTTP status code."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class VoiceNoteResult:
    transcript: str
    summary: str
    consent_at: datetime
    transcription_model: str


def audio_extension(content_type: str | None, data: bytes) -> str:
    """Validate type and magic bytes; return the file extension Groq should see."""
    base = (content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_TYPES.get(base)
    if ext is None:
        raise VoiceNoteError(415, f"Unsupported audio type {base or '(none)'}; allowed: {', '.join(sorted(set(ALLOWED_TYPES)))}")
    if not _SIGNATURES[ext](data[:16]):
        raise VoiceNoteError(415, f"Audio content does not look like {ext}")
    return ext


async def transcribe(data: bytes, ext: str) -> str:
    client = get_async_groq()
    result = await client.audio.transcriptions.create(
        file=(f"voice-note.{ext}", data),
        model=settings.groq_transcribe_model,
        response_format="json",
        temperature=0.0,
    )
    return (getattr(result, "text", "") or "").strip()


async def summarize_for_topics(transcript: str) -> str:
    prompt = f"""You help a pharmaceutical field rep fill in the "Topics Discussed" field of a CRM after an HCP visit.
Summarize the rep's voice note below in at most {MAX_SUMMARY_WORDS} words: products, clinical topics, questions the HCP
raised, concerns, and anything agreed. Use short plain sentences. Do not invent facts that are not in the note.
Return plain text only, no headings or markdown.

Voice note transcript:
\"\"\"{transcript}\"\"\""""
    response, _model = await ainvoke_resilient(prompt, temperature=0.2, fast=True)
    content = response.content if isinstance(response.content, str) else str(response.content)
    return content.strip()


async def process_voice_note(data: bytes, content_type: str | None, consent: bool) -> VoiceNoteResult:
    if not consent:
        raise VoiceNoteError(400, "Recording requires the HCP's consent (consent=true).")
    if not data:
        raise VoiceNoteError(400, "Empty audio.")
    ext = audio_extension(content_type, data)
    consent_at = utcnow()
    try:
        transcript = await transcribe(data, ext)
    except Exception as exc:  # Groq/network failure: never echo the audio, just the error type
        logger.warning("voice note transcription failed: %s", type(exc).__name__)
        raise VoiceNoteError(502, f"Transcription failed ({type(exc).__name__}).") from exc
    finally:
        del data  # drop our reference to the audio as soon as it has been sent
    if not transcript:
        raise VoiceNoteError(422, "No speech detected in the recording.")
    try:
        summary = await summarize_for_topics(transcript)
    except Exception as exc:
        logger.warning("voice note summary failed: %s", type(exc).__name__)
        raise VoiceNoteError(502, f"Summary failed ({type(exc).__name__}).") from exc
    return VoiceNoteResult(transcript, summary, consent_at, settings.groq_transcribe_model)
