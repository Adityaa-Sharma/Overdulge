import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from '../../src/routes/Dashboard'

const { getDashboard } = vi.hoisted(() => ({ getDashboard: vi.fn() }))
const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))

vi.mock('../../src/lib/api', () => ({ getDashboard }))
vi.mock('../../src/lib/session', () => ({ useSession }))

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
}

const EMPTY_TOTALS = { combined: 0, swiggy_food: 0, swiggy_instamart: 0, zepto: 0 }

const EMPTY_RESPONSE = {
  generated_at: '2026-07-23T10:00:00Z',
  has_data: false,
  totals: { this_week_paise: EMPTY_TOTALS, this_month_paise: EMPTY_TOTALS },
  trend: { weekly: [], monthly: [] },
  category_breakdown: { food_delivery_paise: 0, grocery_paise: 0, item_categories_paise: {} },
  top_restaurants: [],
  top_products: [],
  order_stats: {
    swiggy_food: { order_count: 0, avg_order_value_paise: null },
    swiggy_instamart: { order_count: 0, avg_order_value_paise: null },
    zepto: { order_count: 0, avg_order_value_paise: null },
  },
  projection: {
    month: '2026-07',
    spend_to_date_paise: EMPTY_TOTALS,
    days_elapsed: 23,
    days_in_month: 31,
    projected_total_paise: EMPTY_TOTALS,
    label: 'Projection',
  },
  location_lens: [],
}

const POPULATED_RESPONSE = {
  ...EMPTY_RESPONSE,
  has_data: true,
  totals: {
    this_week_paise: { combined: 50000, swiggy_food: 50000, swiggy_instamart: 0, zepto: 0 },
    this_month_paise: { combined: 80000, swiggy_food: 50000, swiggy_instamart: 30000, zepto: 0 },
  },
  trend: {
    weekly: [
      {
        period_start: '2026-07-14',
        combined_paise: 80000,
        swiggy_food_paise: 50000,
        swiggy_instamart_paise: 30000,
        zepto_paise: 0,
      },
    ],
    monthly: [
      {
        period_start: '2026-07-01',
        combined_paise: 80000,
        swiggy_food_paise: 50000,
        swiggy_instamart_paise: 30000,
        zepto_paise: 0,
      },
    ],
  },
  category_breakdown: {
    food_delivery_paise: 50000,
    grocery_paise: 30000,
    item_categories_paise: { grocery: 30000 },
  },
  top_restaurants: [{ name: 'Tasty Bites', spend_paise: 50000, order_count: 1 }],
  top_products: [{ name: 'Milk', spend_paise: 30000, order_count: 1 }],
  order_stats: {
    swiggy_food: { order_count: 1, avg_order_value_paise: 50000 },
    swiggy_instamart: { order_count: 1, avg_order_value_paise: 30000 },
    zepto: { order_count: 0, avg_order_value_paise: null },
  },
  projection: {
    month: '2026-07',
    spend_to_date_paise: { combined: 80000, swiggy_food: 50000, swiggy_instamart: 30000, zepto: 0 },
    days_elapsed: 23,
    days_in_month: 31,
    projected_total_paise: {
      combined: 107826,
      swiggy_food: 67391,
      swiggy_instamart: 40435,
      zepto: 0,
    },
    label: 'Projection',
  },
  location_lens: [{ address_id: 'addr-1', spend_paise: 50000, order_count: 1 }],
}

beforeEach(() => {
  vi.resetAllMocks()
  useSession.mockReturnValue({ logout: vi.fn() })
})

describe('Dashboard', () => {
  it('shows a loading state before the dashboard resolves', () => {
    getDashboard.mockReturnValue(new Promise(() => {}))

    renderDashboard()

    expect(screen.getByRole('heading', { name: /dashboard/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading dashboard/i)).toBeInTheDocument()
  })

  it('renders the empty state without crashing when has_data is false, with no chart mounted', async () => {
    getDashboard.mockResolvedValue(EMPTY_RESPONSE)

    renderDashboard()

    // The zero-data screen names the value rather than announcing absence.
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /see exactly where your food money goes/i }),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByText(/no data yet/i)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /connect your first account/i })).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('Tasty Bites')).not.toBeInTheDocument()
  })

  it('keeps the sample receipt out of the accessibility tree', async () => {
    getDashboard.mockResolvedValue(EMPTY_RESPONSE)

    const { container } = renderDashboard()

    await waitFor(() => expect(container.querySelector('.ghost')).not.toBeNull())
    const ghost = container.querySelector('.ghost')
    expect(ghost).toHaveAttribute('aria-hidden', 'true')
    // Fabricated figures must live inside the hidden region so assistive tech
    // never reads them out as the user's real spend.
    expect(ghost?.textContent).toContain('₹12,480')
  })

  it('shows a retry option when the dashboard fails to load (non-2xx), and recovers on retry', async () => {
    getDashboard.mockRejectedValueOnce(new Error('network error'))

    renderDashboard()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your dashboard/i),
    )

    getDashboard.mockResolvedValue(EMPTY_RESPONSE)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /see exactly where your food money goes/i }),
      ).toBeInTheDocument(),
    )
  })

  it('renders every populated-response section from a mocked GET /dashboard', async () => {
    getDashboard.mockResolvedValue(POPULATED_RESPONSE)

    renderDashboard()

    await waitFor(() => expect(screen.getAllByText('₹800.00').length).toBeGreaterThan(0))
    expect(screen.getByText('Tasty Bites')).toBeInTheDocument()
    expect(screen.getByText('Milk')).toBeInTheDocument()
    expect(screen.getByText(/address addr-1/i)).toBeInTheDocument()
    expect(screen.getAllByText('Swiggy Instamart').length).toBeGreaterThan(0)
  })

  it('shows the literal "Projection" label visibly next to the projected figure (AC-5)', async () => {
    getDashboard.mockResolvedValue(POPULATED_RESPONSE)

    renderDashboard()

    await waitFor(() => expect(screen.getByText('₹1,078.26')).toBeInTheDocument())
    expect(screen.getByText(/projection · day 23 of 31/i)).toBeInTheDocument()
  })
})
