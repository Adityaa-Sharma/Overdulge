import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Recommendations from '../../src/routes/Recommendations'

const { getUsuals, getSuggestions } = vi.hoisted(() => ({
  getUsuals: vi.fn(),
  getSuggestions: vi.fn(),
}))

vi.mock('../../src/lib/api', () => ({ getUsuals, getSuggestions }))

function renderRecommendations() {
  return render(
    <MemoryRouter>
      <Recommendations />
    </MemoryRouter>,
  )
}

const EMPTY_USUALS = { zepto: [], swiggy_instamart: [], swiggy_food: [] }

const ZEPTO_MILK = {
  platform: 'zepto',
  key: 'pv-milk-1',
  name: 'Amul Toned Milk 1L',
  frequency_rank_or_count: 1,
  avg_unit_price_paise: 6800,
  calorie_estimate: null,
  redirect_url: 'https://zepto.example/product/amul-toned-milk-1l/pv-milk-1',
}

const FOOD_BIRYANI = {
  platform: 'swiggy_food',
  key: 'chicken biryani',
  name: 'Chicken Biryani',
  frequency_rank_or_count: 6,
  avg_unit_price_paise: 32000,
  calorie_estimate: 900,
  redirect_url: 'https://swiggy.example/food/search?q=Chicken%20Biryani',
}

const POPULATED_USUALS = {
  zepto: [ZEPTO_MILK],
  swiggy_instamart: [],
  swiggy_food: [FOOD_BIRYANI],
}

const SUGGESTION_FOR_BIRYANI = {
  platform: 'swiggy_food',
  frequent_item: {
    key: 'chicken biryani',
    name: 'Chicken Biryani',
    avg_unit_price_paise: 32000,
    calorie_estimate: 900,
  },
  alternative: {
    name: 'Chicken Biryani (Half)',
    unit_price_paise: 22000,
    calorie_estimate: 650,
    redirect_url: 'https://swiggy.example/food/search?q=Chicken%20Biryani%20Half',
    cheaper: true,
    lower_calorie: true,
  },
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Recommendations', () => {
  it('shows a loading state before usuals resolve', () => {
    getUsuals.mockReturnValue(new Promise(() => {}))

    renderRecommendations()

    expect(screen.getByRole('heading', { name: /usuals & recommendations/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading your usuals/i)).toBeInTheDocument()
  })

  it('shows a retry option when usuals fail to load, and recovers on retry', async () => {
    getUsuals.mockRejectedValueOnce(new Error('network error'))

    renderRecommendations()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your usuals/i))

    getUsuals.mockResolvedValue(EMPTY_USUALS)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
  })

  it('renders the empty state and never calls suggestions when there are no usuals', async () => {
    getUsuals.mockResolvedValue(EMPTY_USUALS)

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /connect an account/i })).toBeInTheDocument()
    expect(getSuggestions).not.toHaveBeenCalled()
  })

  it('renders per-platform usuals with a reorder link pointing at redirect_url', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockReturnValue(new Promise(() => {}))

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/amul toned milk 1l/i)).toBeInTheDocument())
    expect(screen.getByText(/chicken biryani/i)).toBeInTheDocument()

    const reorderLinks = screen.getAllByRole('link', { name: /^reorder$/i })
    expect(reorderLinks).toHaveLength(2)
    expect(reorderLinks[0]).toHaveAttribute('href', ZEPTO_MILK.redirect_url)
    expect(reorderLinks[0]).toHaveAttribute('target', '_blank')
    expect(reorderLinks[0]).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('shows an inline suggestion with its own reorder link when one is found for an item', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockResolvedValue({ suggestions: [SUGGESTION_FOR_BIRYANI] })

    renderRecommendations()

    const alternativeLink = await screen.findByRole('link', {
      name: /reorder chicken biryani \(half\) instead/i,
    })
    expect(screen.getByText(/cheaper & lower-calorie/i)).toBeInTheDocument()

    expect(alternativeLink).toHaveAttribute('href', SUGGESTION_FOR_BIRYANI.alternative.redirect_url)
  })

  it('renders a usual with no matching suggestion with no broken/empty suggestion slot', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    // Only the biryani item gets a suggestion; the milk usual has none.
    getSuggestions.mockResolvedValue({ suggestions: [SUGGESTION_FOR_BIRYANI] })

    renderRecommendations()

    await screen.findByRole('link', { name: /reorder chicken biryani \(half\) instead/i })

    // Only one suggestion came back (for the biryani), so only one "Try ..."
    // note and one badge should render — the milk usual gets no placeholder.
    expect(screen.getAllByText(/^cheaper & lower-calorie$/i)).toHaveLength(1)
    expect(screen.getAllByRole('link', { name: /^reorder$/i })).toHaveLength(2)
    expect(screen.queryAllByRole('link', { name: /instead/i })).toHaveLength(1)
  })

  it('shows a non-blocking retry when suggestions fail to load, without hiding usuals', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockRejectedValueOnce(new Error('boom'))

    renderRecommendations()

    await waitFor(() =>
      expect(screen.getByText(/couldn't load suggested alternatives/i)).toBeInTheDocument(),
    )
    expect(screen.getByText(/amul toned milk 1l/i)).toBeInTheDocument()

    getSuggestions.mockResolvedValue({ suggestions: [SUGGESTION_FOR_BIRYANI] })
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await screen.findByRole('link', { name: /reorder chicken biryani \(half\) instead/i })
  })
})
