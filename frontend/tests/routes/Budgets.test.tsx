import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Budgets from '../../src/routes/Budgets'

const { getBudgets, getBudgetSuggestions, upsertBudget, deleteBudget, ApiError } = vi.hoisted(
  () => {
    class ApiError extends Error {
      status: number
      constructor(message: string, status: number) {
        super(message)
        this.name = 'ApiError'
        this.status = status
      }
    }
    return {
      getBudgets: vi.fn(),
      getBudgetSuggestions: vi.fn(),
      upsertBudget: vi.fn(),
      deleteBudget: vi.fn(),
      ApiError,
    }
  },
)

vi.mock('../../src/lib/api', () => ({
  getBudgets,
  getBudgetSuggestions,
  upsertBudget,
  deleteBudget,
  ApiError,
}))

function renderBudgets() {
  return render(
    <MemoryRouter>
      <Budgets />
    </MemoryRouter>,
  )
}

// The route computes "this month" from the system clock rather than taking it
// as a prop, so fixtures/assertions derive the same value instead of hardcoding
// a month that would drift out of sync with whenever the suite runs.
const now = new Date()
const CURRENT_MONTH = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`

const OK_ONLY_RESPONSE = {
  month: CURRENT_MONTH,
  budgets: [
    { id: 'b-overall', category: null, cap_paise: 800000, spent_paise: 200000, pct: 0.25, status: 'ok' },
    {
      id: 'b-groceries',
      category: 'groceries',
      cap_paise: 300000,
      spent_paise: 100000,
      pct: 0.33,
      status: 'ok',
    },
  ],
}

const NEAR_OVER_RESPONSE = {
  month: CURRENT_MONTH,
  budgets: [
    { id: 'b-overall', category: null, cap_paise: 800000, spent_paise: 780000, pct: 0.975, status: 'near' },
    {
      id: 'b-dining',
      category: 'dining',
      cap_paise: 200000,
      spent_paise: 250000,
      pct: 1.25,
      status: 'over',
    },
  ],
}

const EMPTY_RESPONSE = { month: CURRENT_MONTH, budgets: [] }

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Budgets', () => {
  it('shows a loading state before the budgets resolve', () => {
    getBudgets.mockReturnValue(new Promise(() => {}))

    renderBudgets()

    expect(screen.getByRole('heading', { name: /budgets/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading budgets/i)).toBeInTheDocument()
  })

  it('renders a populated fixture, asserting figures and status appear, without fetching suggestions', async () => {
    getBudgets.mockResolvedValue(OK_ONLY_RESPONSE)

    renderBudgets()

    await waitFor(() => expect(screen.getByText('groceries')).toBeInTheDocument())
    expect(screen.getByText('₹2,000.00 of ₹8,000.00')).toBeInTheDocument()
    expect(screen.getByText('₹1,000.00 of ₹3,000.00')).toBeInTheDocument()
    expect(screen.getAllByText('On track').length).toBe(2)
    expect(getBudgetSuggestions).not.toHaveBeenCalled()
  })

  it('renders the empty-state prompt for a zero-caps fixture without crashing', async () => {
    getBudgets.mockResolvedValue(EMPTY_RESPONSE)

    renderBudgets()

    await waitFor(() => expect(screen.getByText(/no caps set yet/i)).toBeInTheDocument())
    expect(
      screen.getByText(/set an overall monthly cap above, or add a per-category cap below/i),
    ).toBeInTheDocument()
    expect(getBudgetSuggestions).not.toHaveBeenCalled()
  })

  it('fetches and renders suggestions when a category is near/over its cap', async () => {
    getBudgets.mockResolvedValue(NEAR_OVER_RESPONSE)
    getBudgetSuggestions.mockResolvedValue({
      suggestions: [{ category: 'dining', text: 'Cut back on dining out, e.g. Pizza Place (₹800).' }],
    })

    renderBudgets()

    await waitFor(() => expect(screen.getByText('dining')).toBeInTheDocument())
    expect(getBudgetSuggestions).toHaveBeenCalledWith(CURRENT_MONTH)

    await waitFor(() =>
      expect(
        screen.getByText('Cut back on dining out, e.g. Pizza Place (₹800).'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText('Near cap')).toBeInTheDocument()
    expect(screen.getByText('Over cap')).toBeInTheDocument()
  })

  it('does not fetch suggestions when every category is "ok"', async () => {
    getBudgets.mockResolvedValue(OK_ONLY_RESPONSE)

    renderBudgets()

    await waitFor(() => expect(screen.getByText('groceries')).toBeInTheDocument())
    expect(getBudgetSuggestions).not.toHaveBeenCalled()
    expect(screen.queryByText(/where to cut back/i)).not.toBeInTheDocument()
  })

  it('renders nothing for the suggestions panel when the backend returns an empty list', async () => {
    getBudgets.mockResolvedValue(NEAR_OVER_RESPONSE)
    getBudgetSuggestions.mockResolvedValue({ suggestions: [] })

    renderBudgets()

    await waitFor(() => expect(getBudgetSuggestions).toHaveBeenCalled())
    expect(screen.queryByText(/where to cut back/i)).not.toBeInTheDocument()
  })

  it('shows a retry option when budgets fail to load (non-2xx), and recovers on retry', async () => {
    getBudgets.mockRejectedValueOnce(new ApiError('boom', 500))

    renderBudgets()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your budgets/i),
    )

    getBudgets.mockResolvedValue(EMPTY_RESPONSE)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no caps set yet/i)).toBeInTheDocument())
  })
})
