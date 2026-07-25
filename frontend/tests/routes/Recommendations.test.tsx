import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
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

const POPULATED_USUALS = {
  zepto: [
    {
      platform: 'zepto',
      key: 'pv-1',
      name: 'Amul Milk 500ml',
      frequency_rank_or_count: 1,
      avg_unit_price_paise: 6900,
      calorie_estimate: null,
      redirect_url: 'https://zepto.example/pv-1',
    },
  ],
  swiggy_instamart: [
    {
      platform: 'swiggy_instamart',
      key: 'im-1',
      name: 'Brown Bread',
      frequency_rank_or_count: 1,
      avg_unit_price_paise: 5500,
      calorie_estimate: null,
      redirect_url: 'https://instamart.example/im-1',
    },
  ],
  swiggy_food: [
    {
      platform: 'swiggy_food',
      key: 'chicken biryani',
      name: 'Chicken Biryani',
      frequency_rank_or_count: 4,
      avg_unit_price_paise: 32000,
      calorie_estimate: 850,
      redirect_url: 'https://swiggy.example/search?q=Chicken%20Biryani',
    },
  ],
}

const SUGGESTION_FOR_BIRYANI = {
  suggestions: [
    {
      platform: 'swiggy_food',
      frequent_item: {
        key: 'chicken biryani',
        name: 'Chicken Biryani',
        avg_unit_price_paise: 32000,
        calorie_estimate: 850,
      },
      alternative: {
        name: 'Veg Biryani',
        unit_price_paise: 27000,
        calorie_estimate: null,
        redirect_url: 'https://swiggy.example/search?q=Veg%20Biryani',
        cheaper: true,
        lower_calorie: false,
      },
    },
  ],
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Recommendations', () => {
  it('shows a loading state before usuals resolve', () => {
    getUsuals.mockReturnValue(new Promise(() => {}))

    renderRecommendations()

    expect(screen.getByRole('heading', { name: /usuals/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading usuals/i)).toBeInTheDocument()
  })

  it('shows a retry option when usuals fail to load, and recovers on retry', async () => {
    getUsuals.mockRejectedValueOnce(new Error('network error'))

    renderRecommendations()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your usuals/i))

    getUsuals.mockResolvedValue(EMPTY_USUALS)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
  })

  it('renders the empty state and never fetches suggestions when there are no usuals', async () => {
    getUsuals.mockResolvedValue(EMPTY_USUALS)

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /connect an account/i })).toBeInTheDocument()
    expect(getSuggestions).not.toHaveBeenCalled()
  })

  it('renders usuals per platform with reorder links pointing at each redirect_url', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockReturnValue(new Promise(() => {}))

    renderRecommendations()

    await waitFor(() => expect(screen.getByText('Amul Milk 500ml')).toBeInTheDocument())
    expect(screen.getByText('Brown Bread')).toBeInTheDocument()
    expect(screen.getByText('Chicken Biryani')).toBeInTheDocument()

    expect(screen.getByRole('link', { name: /reorder amul milk 500ml on zepto/i })).toHaveAttribute(
      'href',
      'https://zepto.example/pv-1',
    )
    expect(
      screen.getByRole('link', { name: /reorder brown bread on swiggy instamart/i }),
    ).toHaveAttribute('href', 'https://instamart.example/im-1')
    expect(
      screen.getByRole('link', { name: /reorder chicken biryani on swiggy food/i }),
    ).toHaveAttribute('href', 'https://swiggy.example/search?q=Chicken%20Biryani')

    for (const link of screen.getAllByRole('link', { name: /^reorder/i })) {
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    }
  })

  it('shows an inline suggestion for an item that has one, once suggestions resolve', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockResolvedValue(SUGGESTION_FOR_BIRYANI)

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/try instead: veg biryani/i)).toBeInTheDocument())
    expect(screen.getByText('Cheaper')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /reorder veg biryani instead of chicken biryani/i }),
    ).toHaveAttribute('href', 'https://swiggy.example/search?q=Veg%20Biryani')
  })

  it('renders an item with no qualifying suggestion normally, with no broken/empty suggestion slot', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockResolvedValue({ suggestions: [] })

    renderRecommendations()

    await waitFor(() => expect(screen.getByText('Amul Milk 500ml')).toBeInTheDocument())
    await waitFor(() => expect(getSuggestions).toHaveBeenCalledTimes(1))

    expect(screen.queryByText(/try instead/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Cheaper')).not.toBeInTheDocument()
    expect(screen.queryByText('Lower calorie')).not.toBeInTheDocument()
  })

  it('surfaces a suggestions error distinctly from the empty state, without hiding usuals', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockRejectedValueOnce(new Error('boom'))

    renderRecommendations()

    await waitFor(() =>
      expect(screen.getByText(/couldn't load suggested alternatives/i)).toBeInTheDocument(),
    )
    expect(screen.getByText('Amul Milk 500ml')).toBeInTheDocument()

    getSuggestions.mockResolvedValue(SUGGESTION_FOR_BIRYANI)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/try instead: veg biryani/i)).toBeInTheDocument())
  })
})
