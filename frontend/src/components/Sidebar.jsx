import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import { fetchHcps, selectHcp } from "../store/hcpSlice";

export default function Sidebar() {
  const dispatch = useDispatch();

  const { items, selectedId } = useSelector(
    (state) => state.hcps
  );

  useEffect(() => {
    dispatch(fetchHcps());
  }, [dispatch]);

  return (
    <aside className="sidebar">

      <div className="sidebar-header">

        <h1 className="logo">
          AI-First CRM
        </h1>

        <p className="logo-subtitle">
          HCP Interaction Management
        </p>

      </div>

      <div className="sidebar-divider" />

      <div className="hcp-section-title">
        HEALTHCARE PROFESSIONALS
      </div>

      <div className="hcp-list">

        {items.map((hcp) => (

          <button
            key={hcp.id}
            type="button"
            className={`hcp-card ${
              selectedId === hcp.id ? "active" : ""
            }`}
            onClick={() => dispatch(selectHcp(hcp.id))}
          >

            <div className="hcp-content">

              <h3 className="hcp-name">
                Dr. {hcp.name.replace(/^Dr\.?\s*/i, "")}
              </h3>

              <div className="hcp-specialty">
                {hcp.specialty}
              </div>

              <div className="hcp-hospital">
                {hcp.institution}
              </div>

              <span className="tier-badge">
                Tier {hcp.tier}
              </span>

            </div>

          </button>

        ))}

      </div>

    </aside>
  );
}