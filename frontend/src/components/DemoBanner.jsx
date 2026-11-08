import { useAppMeta } from '../lib/meta'

// Shown on every screen (including sign-in) when the backend runs with DEMO_MODE=true.
export default function DemoBanner() {
  const meta = useAppMeta()
  if (!meta?.demo_mode) return null
  return (
    <div className="demo-banner" role="note" aria-label="Demo mode">
      <strong>Demo mode</strong>
      <span>
        Sample data resets nightly. AI features have a shared daily limit
        {meta.llm_daily_cap ? ` of ${meta.llm_daily_cap} requests` : ''}. User management is read-only.
      </span>
    </div>
  )
}
