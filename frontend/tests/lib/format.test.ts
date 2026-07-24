import { describe, expect, it } from 'vitest'
import { formatKcalEstimate, formatPaise } from '../../src/lib/format'

describe('formatPaise', () => {
  it('formats integer paise as INR with comma grouping and two decimals', () => {
    expect(formatPaise(123456)).toBe('₹1,234.56')
  })

  it('formats zero paise as ₹0.00', () => {
    expect(formatPaise(0)).toBe('₹0.00')
  })
})

describe('formatKcalEstimate', () => {
  it('formats a kcal figure with a leading "~" and a spelled-out estimate tag (FR-6.3/AC-3)', () => {
    expect(formatKcalEstimate(1234)).toBe('~1,234 kcal (estimate)')
  })

  it('formats zero kcal the same way, never omitting the estimate marker', () => {
    expect(formatKcalEstimate(0)).toBe('~0 kcal (estimate)')
  })
})
