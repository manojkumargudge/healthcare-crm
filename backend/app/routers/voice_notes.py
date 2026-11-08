from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import VoiceNoteSummary
from app.voice_notes import MAX_AUDIO_BYTES, VoiceNoteError, process_voice_note

router = APIRouter(prefix="/api/voice-notes", tags=["Voice notes"])


@router.post("/summarize", response_model=VoiceNoteSummary)
async def summarize_voice_note(request: Request, consent: bool = Query(False)):
    """Transcribe a short voice note and summarize it for "Topics Discussed".

    Send the raw audio as the request body with its Content-Type (e.g. audio/webm),
    and consent=true once the HCP has agreed. The audio is held in memory only
    and never stored; the response is not stored either.
    """
    if not consent:
        raise HTTPException(400, "Recording requires the HCP's consent (consent=true).")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"Audio larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
    buf = bytearray()
    async for chunk in request.stream():  # raw body, in memory: no multipart temp files
        buf.extend(chunk)
        if len(buf) > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"Audio larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
    try:
        result = await process_voice_note(bytes(buf), request.headers.get("content-type"), consent)
    except VoiceNoteError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    finally:
        buf.clear()
    return VoiceNoteSummary(
        transcript=result.transcript,
        summary=result.summary,
        consent_at=result.consent_at,
        transcription_model=result.transcription_model,
    )
