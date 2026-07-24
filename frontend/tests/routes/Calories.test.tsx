import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
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
  // Trend figures deliberately don't collide with the totals below — this is
  // a fixture for asserting distinct rendered text, not a realistic dataset.
  totals: { this_week_estimate_kcal: 4200, this_month_estimate_kcal: 15800 },
  trend: {
    weekly: [
      { period_start: '2026-07-07', estimate_kcal: 3600 },
      { period_start: '2026-07-14', estimate_kcal: 3900 },
    ],
  },
}

beforeEach(() => {
  vi.resetAllMocks()
  getCaloriesCommentary.mockReturnValue(new Promise(() => {}))
})

describe('Calories', () => {
  it('shows a loading state before the rollup resolves', () => {
    getCalories.mockReturnValue(new Promise(() => {}))

    renderCalories()

    expect(screen.getByRole('heading', { name: /calories/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading calorie estimates/i)).toBeInTheDocument()
  })

  it('renders the empty state without crashing when has_data is false', async () => {
    getCalories.mockResolvedValue(EMPTY_RESPONSE)

    renderCalories()

    await waitFor(() => expect(screen.getByText(/no estimates yet/i)).toBeInTheDocument())
    expect(screen.queryByText(/~/)).not.toBeInTheDocument()
  })

  it('shows a retry option when the rollup fails to load (non-2xx), and recovers on retry', async () => {
    getCalories.mockRejectedValueOnce(new Error('network error'))

    renderCalories()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your calorie estimates/i),
    )

    getCalories.mockResolvedValue(EMPTY_RESPONSE)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no estimates yet/i)).toBeInTheDocument())
  })

  it('renders totals and trend from a populated fixture, every kcal figure carrying the estimate marker', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)

    renderCalories()

    await waitFor(() => expect(screen.getByText('~4,200 kcal')).toBeInTheDocument())
    expect(screen.getByText('~15,800 kcal')).toBeInTheDocument()
    // Trend bars carry the same marker via the chart's accessible table/aria-label.
    expect(screen.getByText('~3,900 kcal')).toBeInTheDocument()
  })

  it('renders the commentary blurb once its own fetch resolves, without blocking the rollup', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)
    let resolveCommentary: (value: { blurb: string }) => void = () => {}
    getCaloriesCommentary.mockReturnValue(
      new Promise((resolve) => {
        resolveCommentary = resolve
      }),
    )

    renderCalories()

    await waitFor(() => expect(screen.getByText('~4,200 kcal')).toBeInTheDocument())
    expect(screen.getByLabelText(/loading commentary/i)).toBeInTheDocument()
    expect(screen.queryByText(/big spender/i)).not.toBeInTheDocument()

    resolveCommentary({ blurb: "Big spender energy this week — your wallet noticed." })

    await waitFor(() =>
      expect(
        screen.getByText("Big spender energy this week — your wallet noticed."),
      ).toBeInTheDocument(),
    )
  })

  it('shows a retry option when the commentary fetch fails, independent of the rollup', async () => {
    getCalories.mockResolvedValue(POPULATED_RESPONSE)
    getCaloriesCommentary.mockRejectedValueOnce(new Error('boom'))

    renderCalories()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load this week's commentary/i),
    )
    expect(screen.getByText('~4,200 kcal')).toBeInTheDocument()

    getCaloriesCommentary.mockResolvedValue({ blurb: 'Back on track, wallet-wise.' })
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText('Back on track, wallet-wise.')).toBeInTheDocument())
  })
})
