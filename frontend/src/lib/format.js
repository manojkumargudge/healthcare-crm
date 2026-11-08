// Display helpers shared by the history list and the sentiment pulse strip.

export const SENTIMENTS = ['Positive', 'Neutral', 'Negative']

/** Sentiment score for display: "—" when unknown (e.g. label set manually), else signed 2 dp. */
export function formatScore(score) {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return '—'
  const n = Number(score)
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`
}

export function sentimentClass(sentiment) {
  if (sentiment === 'Positive') return 'positive'
  if (sentiment === 'Negative') return 'negative'
  return 'neutral'
}
