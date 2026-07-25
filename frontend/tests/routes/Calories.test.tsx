import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Calories from '../../src/routes/Calories'

const { getCalories, getCaloriesCommentary } = vi.hoisted(() => ({
  getCalories: vi.fn(),
  getCaloriesCommentary: vi.fn(),
}))

vi.mock('../../src/lib/api', () => ({ getCalories, getCaloriesCommentary }))

function renderCalories() {
  return render(
    <MemoryRouter>
      <Calories />
    </MemoryRouter>,
  )
}

const EMPTY_RESPONSE = {
  generated_at: '2026-07-23T10:00:00Z',
  has_data: false,
  totals: { this_week_estimate_kcal: 0, this_month_estimate_kcal: 0 },
  trend: { weekly: [] },
}

const POPULATED_RESPONSE = {
  generated_at: '2026-07-23T10:00:00Z',
  has_data: true,
  totals: { this_week_estimate_kcal: 4200, this_month_estimate_kcal: 15800 },
  trend: {
    weekly: [
      { period_start: '2026-07-14', estimate_kcal: 3900 },
      { period_start: '2026-07-21', estimate_kcal: 4100 },
    ],
  },
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Calories', () => {
  it('shows a loading state before the rollup resolves', () => {
    getCalories.mockReturnValue(new Promise(() => {}))

    renderCalories()

    expect(screen.getByRole('heading', { name: /calories/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading calorie estimates/i)).toBeInTheDocument()
  })

  it('shows a retry option when the rollup fails to load (non-2xx), and recovers on retry', async () => {
    getCalories.mockRejectedValueOnce(new Error('network error'))

    renderCalories()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your calorie estimates/i),
    )

    getCalories.mockResolvedValue(EMPTY_RESPONSE)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no orders to estimate yet/i)).toBeInTheDocument())
    expect(getCaloriesCommentary).not.toHaveBeenCalled()
  })

  it('renders the empty state without fetching commentary when has_data is false', async () => {
    getCalories.mockResolvedValue(EMPTY_RESPONSE)

    renderCalories()

    await waitFor(() => expect(screen.getByText(/no orders to estimate yet/i)).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /connect an account/i })).toBeInTheDocument()
    expect(getCaloriesCommentary).not.toHaveBeenCalled()
  })

  it('renders totals and trend with every kcal figure marked as an estimate', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)
    getCaloriesCommentary.mockReturnValue(new Promise(() => {}))

    renderCalories()

    await waitFor(() => expect(screen.getByText('~4,200 kcal (estimate)')).toBeInTheDocument())
    expect(screen.getByText('~15,800 kcal (estimate)')).toBeInTheDocument()
    // The trend chart's accessible name carries the same marked figures.
    expect(
      screen.getByRole('img', { name: /~3,900 kcal \(estimate\).*~4,100 kcal \(estimate\)/s }),
    ).toBeInTheDocument()
  })

  it('fetches commentary lazily after the rollup renders, without blocking it', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)
    let resolveCommentary: (value: { blurb: string }) => void = () => {}
    getCaloriesCommentary.mockReturnValue(
      new Promise((resolve) => {
        resolveCommentary = resolve
      }),
    )

    renderCalories()

    await waitFor(() => expect(screen.getByLabelText(/loading commentary/i)).toBeInTheDocument())
    expect(screen.getByText('~4,200 kcal (estimate)')).toBeInTheDocument()
    expect(getCaloriesCommentary).toHaveBeenCalledTimes(1)

    resolveCommentary({ blurb: 'Four orders this week — your wallet noticed before your stomach did.' })

    await waitFor(() =>
      expect(
        screen.getByText('Four orders this week — your wallet noticed before your stomach did.'),
      ).toBeInTheDocument(),
    )
  })

  it('shows a retry option when commentary fails to load, without affecting the totals/trend', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)
    getCaloriesCommentary.mockRejectedValueOnce(new Error('boom'))

    renderCalories()

    await waitFor(() =>
      expect(screen.getByText(/couldn't load this week's commentary/i)).toBeInTheDocument(),
    )
    expect(screen.getByText('~4,200 kcal (estimate)')).toBeInTheDocument()

    getCaloriesCommentary.mockResolvedValue({ blurb: 'Back on track, roughly speaking.' })
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() =>
      expect(screen.getByText('Back on track, roughly speaking.')).toBeInTheDocument(),
    )
  })
})
