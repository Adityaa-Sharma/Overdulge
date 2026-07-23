/** Formats integer paise (BRD §2.5) as an INR display string, e.g. `formatPaise(123456)` -> "₹1,234.56". */
export function formatPaise(paise: number): string {
  const rupees = paise / 100
  return `₹${rupees.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Formats an ISO date (`YYYY-MM-DD` or full timestamp) as a short human-readable date, e.g. "22 Jul". */
export function formatShortDate(isoDate: string): string {
  return new Date(isoDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
