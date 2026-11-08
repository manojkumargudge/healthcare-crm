import { formatScore, sentimentClass } from '../lib/format'

export default function PulseStrip({ interactions }) {
  if (!interactions || interactions.length === 0) {
    return (
      <div className="pulse-strip">
        <span className="pulse-label">No interaction history available</span>
      </div>
    )
  }

  const recent = [...interactions].slice(0, 12).reverse()

  return (
    <div className="pulse-strip">
      {recent.map((interaction) => {
        const score = interaction.sentiment_score
        const unscored = score === null || score === undefined
        // No score (e.g. label set manually): a short, hollow bar instead of an invented value.
        const height = unscored ? 10 : 10 + Math.min(Math.abs(score), 1) * 22
        return (
          <div
            key={interaction.id}
            data-testid="pulse-bar"
            className={`pulse-bar ${sentimentClass(interaction.hcp_sentiment)}${unscored ? ' unscored' : ''}`}
            style={{ height: `${height}px` }}
            title={`${interaction.interaction_date} • ${interaction.hcp_sentiment || 'No sentiment'} • score ${formatScore(score)}`}
          />
        )
      })}
      <span className="pulse-label">Recent sentiment pulse</span>
    </div>
  )
}
