import { useEffect, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { sendChatMessage } from "../store/chatSlice";
import { fetchInteractions } from "../store/interactionsSlice";

export default function ChatInterface() {
  const dispatch = useDispatch();

  const { sessionId, messages, status } = useSelector((s) => s.chat);

  const hcpId = useSelector((s) => s.hcps.selectedId);

  const hcp = useSelector((s) =>
    s.hcps.items.find((h) => h.id === hcpId)
  );

  const [draft, setDraft] = useState("");

  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages]);

  const send = async (e) => {
    e.preventDefault();

    if (!draft.trim()) return;

    const res = await dispatch(
      sendChatMessage({
        sessionId,
        message: draft,
        hcpId,
      })
    );

    setDraft("");

    if (res.payload?.interaction) {
      dispatch(fetchInteractions(hcpId));
    }
  };

  return (
    <div className="chat-card">

      <div className="chat-header">

        <h2>AI Assistant</h2>

        <p>
          Describe your interaction naturally. The AI will summarize,
          extract products, samples and automatically log it.
        </p>

      </div>

      <div className="chat-messages">

        {messages.length === 0 && (

          <div className="chat-example">

            <strong>Example</strong>

            <p>
              I met Dr. {hcp?.name || "Smith"} today. We discussed
              CardioMax, shared a clinical study, left three samples
              and scheduled a follow-up next week.
            </p>

          </div>

        )}

        {messages.map((m, index) => (

          <div key={index}>

            {m.toolCalls?.length > 0 && (

              <div className="tool-trace">

                {m.toolCalls.map((t) => t.tool).join(" → ")}

              </div>

            )}

            <div className={`chat-bubble ${m.role}`}>

              {m.content}

            </div>

          </div>

        ))}

        {status === "loading" && (

          <div className="chat-bubble assistant">

            Thinking...

          </div>

        )}

        <div ref={bottomRef} />

      </div>

      <form
        className="chat-footer"
        onSubmit={send}
      >

        <input
          value={draft}
          onChange={(e) =>
            setDraft(e.target.value)
          }
          placeholder={
            hcpId
              ? "Describe today's interaction..."
              : "Select an HCP first"
          }
        />

        <button className="btn-primary">

          Log

        </button>

      </form>

    </div>
  );
}