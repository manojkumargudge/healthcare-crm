const sentimentClass = (sentiment) => {
  if (sentiment === "Positive") return "positive";
  if (sentiment === "Negative") return "negative";
  return "neutral";
};

export default function PulseStrip({ interactions }) {
  if (!interactions || interactions.length === 0) {
    return (
      <div className="pulse-strip">
        <span className="pulse-label">
          No interaction history available
        </span>
      </div>
    );
  }

  const recent = [...interactions]
    .slice(0, 12)
    .reverse();

  return (
    <div className="pulse-strip">

      {recent.map((interaction) => {

        const score =
          interaction.sentiment_score ?? 0.4;

        const height =
          10 + Math.min(Math.abs(score), 1) * 22;

        return (
          <div
            key={interaction.id}
            className={`pulse-bar ${sentimentClass(
              interaction.hcp_sentiment
            )}`}
            style={{
              height: `${height}px`,
            }}
            title={`${interaction.interaction_date} • ${
              interaction.hcp_sentiment || "Neutral"
            }`}
          />
        );
      })}

      <span className="pulse-label">
        Recent sentiment pulse
      </span>

    </div>
  );
}