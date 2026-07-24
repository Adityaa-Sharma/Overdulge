/** Formats integer paise (BRD §2.5) as an INR display string, e.g. `formatPaise(123456)` -> "₹1,234.56". */
export function formatPaise(paise: number): string {
  const rupees = paise / 100
  return `₹${rupees.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Formats an ISO date (`YYYY-MM-DD` or full timestamp) as a short human-readable date, e.g. "22 Jul". */
export function formatShortDate(isoDate: string): string {
  return new Date(isoDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/**
 * Formats an integer kcal figure with a leading "~" and unit, e.g.
 * `formatKcalEstimate(1234)` -> "~1,234 kcal". Every calorie figure FR-6
 * renders is LLM-estimated, never measured — this prefix is the one
 * consistent "estimate" marker applied everywhere a kcal number is shown
 * (FR-6.3/AC-3), including inside SVG `<text>`/`<title>` and the
 * screen-reader trend table, where a wrapping badge component can't render.
 */
export function formatKcalEstimate(kcal: number): string {
  return `~${kcal.toLocaleString('en-IN')} kcal`
}
