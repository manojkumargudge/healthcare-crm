import { useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { editInteraction } from "../store/interactionsSlice";

function sentimentClass(sentiment) {
  if (sentiment === "Positive") return "positive";
  if (sentiment === "Negative") return "negative";
  return "neutral";
}

export default function InteractionHistory() {
  const dispatch = useDispatch();

  const items = useSelector((s) => s.interactions.items);

  const [editingId, setEditingId] = useState(null);
  const [draftNotes, setDraftNotes] = useState("");

  const startEdit = (item) => {
    setEditingId(item.id);
    setDraftNotes(item.raw_notes || "");
  };

  const saveEdit = (item) => {
    dispatch(
      editInteraction({
        id: item.id,
        updates: {
          raw_notes: draftNotes,
          edit_reason: "Corrected from UI",
        },
      })
    );

    setEditingId(null);
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

      {items.map((item) => (

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

            </div>

            {item.hcp_sentiment && (

              <span
                className={`sentiment-tag ${sentimentClass(
                  item.hcp_sentiment
                )}`}
              >
                {item.hcp_sentiment}
              </span>

            )}

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

              <textarea
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

                  {item.products_discussed.join(", ")}

                </p>

              )}

              {item.compliance_flag && (

                <div className="compliance-flag">

                  ⚠ {item.compliance_notes}

                </div>

              )}

              <button
                className="btn-secondary"
                style={{
                  marginTop: 18,
                }}
                onClick={() => startEdit(item)}
              >
                Edit Interaction
              </button>

            </>

          )}

        </div>

      ))}

    </div>
  );
}