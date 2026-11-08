import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'

export const MAX_SECONDS = 180
export const MAX_BYTES = 10 * 1024 * 1024
const PREFERRED_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

function pickMimeType() {
  const MR = globalThis.MediaRecorder
  if (!MR) return ''
  if (typeof MR.isTypeSupported !== 'function') return ''
  return PREFERRED_TYPES.find((t) => MR.isTypeSupported(t)) || ''
}

const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

/**
 * "Summarize from Voice Note (Requires Consent)" (brief mockup).
 * Recording is impossible until the consent box is ticked. The recording is sent once
 * for transcription + summary and never stored; the summary goes to Topics Discussed.
 */
export default function VoiceNoteRecorder({ onSummary, disabled = false }) {
  const [open, setOpen] = useState(false)
  const [consent, setConsent] = useState(false)
  const [phase, setPhase] = useState('idle') // idle | recording | uploading | done | error
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState('')
  const [transcript, setTranscript] = useState('')
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    clearInterval(timerRef.current)
  }
  useEffect(() => stopStream, [])

  const upload = async (blob) => {
    if (blob.size > MAX_BYTES) {
      setPhase('error')
      setError('Recording is too large (max 10 MB). Try a shorter note.')
      return
    }
    setPhase('uploading')
    try {
      const res = await api.summarizeVoiceNote(blob)
      setTranscript(res.transcript)
      onSummary(res.summary, { consentAt: res.consent_at, transcript: res.transcript })
      setPhase('done')
    } catch (err) {
      setPhase('error')
      setError(err.message)
    }
  }

  const start = async () => {
    setError('')
    setTranscript('')
    if (!globalThis.navigator?.mediaDevices?.getUserMedia || !globalThis.MediaRecorder) {
      setPhase('error')
      setError("Recording isn't supported in this browser.")
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const mimeType = pickMimeType()
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      recorderRef.current = recorder
      chunksRef.current = []
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data) }
      recorder.onstop = () => {
        stopStream()
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || 'audio/webm' })
        chunksRef.current = []
        upload(blob)
      }
      recorder.start()
      setSeconds(0)
      setPhase('recording')
      timerRef.current = setInterval(() => {
        setSeconds((s) => {
          if (s + 1 >= MAX_SECONDS) recorderRef.current?.state === 'recording' && recorderRef.current.stop()
          return s + 1
        })
      }, 1000)
    } catch (err) {
      stopStream()
      setPhase('error')
      setError(err?.name === 'NotAllowedError' ? 'Microphone permission was denied.' : `Couldn’t start recording: ${err.message}`)
    }
  }

  const stop = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
  }

  return (
    <div className="voice-note">
      <button type="button" className="btn-voice" onClick={() => setOpen((v) => !v)} disabled={disabled} aria-expanded={open}>
        <span aria-hidden="true">🎙</span> Summarize from Voice Note (Requires Consent)
      </button>

      {open && (
        <div className="voice-panel" role="group" aria-label="Voice note">
          <label className="voice-consent">
            <input
              type="checkbox"
              checked={consent}
              disabled={phase === 'recording' || phase === 'uploading'}
              onChange={(e) => setConsent(e.target.checked)}
            />
            HCP consents to this recording being sent to a third-party AI service for transcription. Audio is not stored.
          </label>
          <p className="voice-hint">Up to {MAX_SECONDS / 60} minutes.</p>

          <div className="voice-controls">
            {phase === 'recording' ? (
              <button type="button" className="btn-small btn-stop" onClick={stop}>
                <span aria-hidden="true">■</span> Stop ({fmt(seconds)})
              </button>
            ) : (
              <button type="button" className="btn-small" onClick={start} disabled={!consent || phase === 'uploading'}>
                <span aria-hidden="true">●</span> Record
              </button>
            )}
            {phase === 'recording' && <span className="voice-live" role="status">Recording…</span>}
            {phase === 'uploading' && <span className="voice-status" role="status">Transcribing and summarizing…</span>}
            {phase === 'done' && <span className="voice-status ok" role="status">Summary added to Topics Discussed.</span>}
          </div>

          {phase === 'error' && <p className="voice-error" role="alert">{error}</p>}
          {transcript && (
            <details className="voice-transcript">
              <summary>Transcript (not saved)</summary>
              <p>{transcript}</p>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
