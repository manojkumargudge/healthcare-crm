import { useMemo, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { editInteraction } from "../store/interactionsSlice";
import { formatScore, sentimentClass, withOriginal } from "../lib/format";


export default function InteractionHistory() {
  const dispatch = useDispatch();

  const items = useSelector((s) => s.interactions.items);
  const user = useSelector((s) => s.auth?.user);

  const [editingId, setEditingId] = useState(null);
  const [draftNotes, setDraftNotes] = useState("");
  const [editError, setEditError] = useState("");
  const [repFilter, setRepFilter] = useState("");

  // Managers and admins see everyone's interactions, so show whose each one is and
  // let them filter by rep. Reps only ever see their own rows.
  const seesAllReps = user?.role === "manager" || user?.role === "admin";
  const reps = useMemo(
    () => [...new Set(items.map((i) => i.owner_email).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [items]
  );
  const activeFilter = seesAllReps && reps.includes(repFilter) ? repFilter : "";
  const visible = activeFilter ? items.filter((i) => i.owner_email === activeFilter) : items;

  // Same rule as the API: admins edit anything; everyone else only their own rows
  // (managers can read other reps' interactions but not change them).
  const canEdit = (item) => user?.role === "admin" || (user && item.owner_id === user.id);

  const startEdit = (item) => {
    setEditingId(item.id);
    setDraftNotes(item.raw_notes || "");
    setEditError("");
  };

  const saveEdit = async (item) => {
    try {
      await dispatch(
        editInteraction({
          id: item.id,
          updates: {
            raw_notes: draftNotes,
            edit_reason: "Corrected from UI",
          },
        })
      ).unwrap();
      setEditingId(null);
      setEditError("");
    } catch (err) {
      // Keep the editor open with the rep's text, and say why it failed.
      setEditError(`Couldn’t save: ${err.message}`);
    }
  };

  if (items.length === 0) {
    return (
      <div className="history-card">
        <div className="empty-state">
          No interactions logged yet.
        </div>
      </div>
    );
  }

  return (
    <div className="history-card">

      {seesAllReps && (
        <div className="history-filter">
          <label htmlFor="rep-filter">Rep</label>
          <select id="rep-filter" value={activeFilter} onChange={(e) => setRepFilter(e.target.value)}>
            <option value="">All reps ({items.length})</option>
            {reps.map((email) => (
              <option key={email} value={email}>
                {email} ({items.filter((i) => i.owner_email === email).length})
              </option>
            ))}
          </select>
        </div>
      )}

      {visible.map((item) => (

        <div
          className="history-item"
          key={item.id}
        >

          <div className="history-item-top">

            <div>

              <div className="history-type">

                {item.interaction_type}

              </div>

              <div className="history-date">

                {item.interaction_date}

              </div>

              {seesAllReps && (
                <div className="history-rep">Rep: {item.owner_email || "unknown"}</div>
              )}

            </div>

            <div className="history-sentiment">
              {item.hcp_sentiment && (
                <span className={`sentiment-tag ${sentimentClass(item.hcp_sentiment)}`}>
                  {item.hcp_sentiment}
                </span>
              )}
              <span className="sentiment-score" title="AI sentiment score (−1 to +1); — when not scored">
                Score {formatScore(item.sentiment_score)}
              </span>
            </div>

          </div>

          {item.ai_summary && (

            <p
              style={{
                marginTop: 14,
                lineHeight: 1.6,
              }}
            >
              {item.ai_summary}
            </p>

          )}

          {editingId === item.id ? (

            <>

              {editError && <p className="form-error" role="alert">{editError}</p>}

              <textarea
                aria-label="Edit notes"
                value={draftNotes}
                onChange={(e) =>
                  setDraftNotes(e.target.value)
                }
              />

              <div
                style={{
                  display: "flex",
                  gap: 12,
                  marginTop: 14,
                }}
              >

                <button
                  className="btn-primary"
                  onClick={() => saveEdit(item)}
                >
                  Save
                </button>

                <button
                  className="btn-secondary"
                  onClick={() =>
                    setEditingId(null)
                  }
                >
                  Cancel
                </button>

              </div>

            </>

          ) : (

            <>

              {item.raw_notes && (

                <p
                  style={{
                    color: "#6b7280",
                    marginTop: 14,
                    fontStyle: "italic",
                  }}
                >
                  "{item.raw_notes}"
                </p>

              )}

              {item.products_discussed?.length > 0 && (

                <p
                  style={{
                    marginTop: 14,
                  }}
                >

                  <strong>Products:</strong>{" "}

                  {item.products_discussed.map((p) => withOriginal(p, "products_discussed", item.product_matches)).join(", ")}

                </p>

              )}

              {item.samples_dropped?.length > 0 && (
                <p style={{ marginTop: 8 }}>
                  <strong>Samples:</strong>{" "}
                  {item.samples_dropped
                    .map((sd) => `${withOriginal(sd.product, "samples_dropped", item.product_matches)} × ${sd.qty}`)
                    .join(", ")}
                </p>
              )}

              {item.unmatched_products?.length > 0 && (
                <p style={{ marginTop: 8 }} className="unmatched-products">
                  <strong>Not in product list:</strong> {item.unmatched_products.join(", ")}
                </p>
              )}

              {item.voice_note_consent && (
                <p style={{ marginTop: 8 }} className="voice-consent-note">
                  🎙 Voice note: HCP consent recorded
                  {item.voice_note_consent_at ? ` ${new Date(item.voice_note_consent_at).toLocaleString()}` : ""}
                </p>
              )}

              {item.attendees?.length > 0 && (
                <p style={{ marginTop: 8 }}>
                  <strong>Attendees:</strong> {item.attendees.join(", ")}
                </p>
              )}

              {item.materials_shared?.length > 0 && (
                <p style={{ marginTop: 8 }}>
                  <strong>Materials:</strong> {item.materials_shared.join(", ")}
                </p>
              )}

              {item.compliance_flag && (

                <div className="compliance-flag">

                  ⚠ {item.compliance_notes}

                </div>

              )}

              {canEdit(item) && (
                <button
                className="btn-secondary"
                style={{
                  marginTop: 18,
                }}
                onClick={() => startEdit(item)}
              >
                Edit Interaction
              </button>
              )}

            </>

          )}

        </div>

      ))}

    </div>
  );
}