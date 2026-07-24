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
 * Formats an estimated kcal figure with a leading "~" and a spelled-out
 * "(estimate)" tag (FR-6.3/AC-3: every calorie figure must be visibly
 * labeled as an estimate). Used for every kcal number this feature renders,
 * e.g. `formatKcalEstimate(1234)` -> "~1,234 kcal (estimate)".
 */
export function formatKcalEstimate(kcal: number): string {
  return `~${kcal.toLocaleString('en-IN')} kcal (estimate)`
}
