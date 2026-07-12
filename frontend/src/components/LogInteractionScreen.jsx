import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import { fetchInteractions } from "../store/interactionsSlice";

import PulseStrip from "./PulseStrip";
import StructuredForm from "./StructuredForm";
import ChatInterface from "./ChatInterface";
import InteractionHistory from "./InteractionHistory";

export default function LogInteractionScreen() {
  const dispatch = useDispatch();

  const hcpId = useSelector((s) => s.hcps.selectedId);

  const hcp = useSelector((s) =>
    s.hcps.items.find((h) => h.id === hcpId)
  );

  const interactions = useSelector(
    (s) => s.interactions.items
  );

  useEffect(() => {
    if (hcpId) {
      dispatch(fetchInteractions(hcpId));
    }
  }, [hcpId, dispatch]);

  if (!hcp) {
    return (
      <main className="main">
        <div className="empty-state">
          Select a Healthcare Professional to begin.
        </div>
      </main>
    );
  }

  return (
    <main className="main">

      <div className="page-header">

        <span className="page-subtitle">
          HCP INTERACTION
        </span>

        <h1 className="page-title">
          Log HCP Interaction
        </h1>

        <p className="page-description">
          Capture details of your meeting with
          <strong> {hcp.name}</strong>.
        </p>

      </div>

      <PulseStrip interactions={interactions} />

      <div className="interaction-layout">

        <div className="interaction-form-panel">

          <StructuredForm />

        </div>

        <div className="interaction-chat-panel">

          <ChatInterface />

        </div>

      </div>

      <div className="interaction-history-section">

        <h2 className="section-title">
          Interaction History
        </h2>

        <InteractionHistory />

      </div>

    </main>
  );
}