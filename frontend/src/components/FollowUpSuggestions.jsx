import { useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { acceptFollowUp, dismissFollowUp, generateSuggestions } from '../store/followUpsSlice'

const EMPTY = []

/**
 * "AI Suggested Follow-ups:" at the bottom of the Interaction Details card (as in the
 * brief mockup): "+ action" links for the interaction just logged; click one to add it.
 */
export default function FollowUpSuggestions({ interactionId }) {
  const dispatch = useDispatch()
  const items = useSelector((s) => (interactionId ? s.followUps.byInteraction[interactionId] : null) || EMPTY)
  const status = useSelector((s) => s.followUps.status)
  const error = useSelector((s) => s.followUps.error)
  const rateLimited = useSelector((s) => s.followUps.rateLimited)

  useEffect(() => {
    if (interactionId) dispatch(generateSuggestions(interactionId))
  }, [interactionId, dispatch])

  const suggested = items.filter((f) => f.status === 'Suggested')
  const added = items.filter((f) => f.status === 'Pending')

  return (
    <section className="follow-ups" aria-label="AI Suggested Follow-ups">
      <div className="follow-ups-header">
        <h3>AI Suggested Follow-ups:</h3>
        {interactionId && (
          <button
            type="button"
            className="btn-link"
            onClick={() => dispatch(generateSuggestions(interactionId))}
            disabled={status === 'loading'}
          >
            {status === 'loading' ? 'Thinking…' : 'Refresh'}
          </button>
        )}
      </div>

      {!interactionId && <p className="follow-ups-empty">Suggestions appear here after you log the interaction.</p>}
      {interactionId && status === 'failed' && (
        <p className="follow-ups-error" role="alert">{rateLimited ? error : `Couldn’t get suggestions: ${error}`}</p>
      )}
      {interactionId && status !== 'failed' && error && <p className="follow-ups-error">{error}</p>}
      {interactionId && status !== 'loading' && status !== 'failed' && suggested.length === 0 && added.length === 0 && (
        <p className="follow-ups-empty">No suggestions for this interaction.</p>
      )}

      <ul className="follow-ups-list">
        {suggested.map((f) => (
          <li key={f.id} className="follow-up suggested">
            <button
              type="button"
              className="follow-up-add"
              onClick={() => dispatch(acceptFollowUp({ id: f.id }))}
              title={`Add this follow-up (due ${f.due_date})`}
            >
              + {f.reason}
            </button>
            <button
              type="button"
              className="follow-up-dismiss"
              aria-label={`Dismiss: ${f.reason}`}
              onClick={() => dispatch(dismissFollowUp({ id: f.id }))}
            >
              ×
            </button>
          </li>
        ))}
        {added.map((f) => (
          <li key={f.id} className="follow-up added" title={`Added, due ${f.due_date}`}>
            <span className="follow-up-check" aria-hidden="true">✓</span> {f.reason}
            <span className="follow-up-due">due {f.due_date}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
