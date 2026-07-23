import { describe, expect, it } from 'vitest'
import { formatPaise } from '../../src/lib/format'

describe('formatPaise', () => {
  it('formats integer paise as INR with comma grouping and two decimals', () => {
    expect(formatPaise(123456)).toBe('₹1,234.56')
  })

  it('formats zero paise as ₹0.00', () => {
    expect(formatPaise(0)).toBe('₹0.00')
  })
})
