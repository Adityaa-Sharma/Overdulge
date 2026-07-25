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

const POPULATED_USUALS = {
  zepto: [
    {
      platform: 'zepto',
      key: 'variant-1',
      name: 'Amul Milk 1L',
      frequency_rank_or_count: 1,
      avg_unit_price_paise: 6500,
      calorie_estimate: null,
      redirect_url: 'https://zepto.example/product/variant-1',
    },
    {
      platform: 'zepto',
      key: 'variant-2',
      name: 'Brown Bread',
      frequency_rank_or_count: 2,
      avg_unit_price_paise: 4500,
      calorie_estimate: null,
      redirect_url: 'https://zepto.example/product/variant-2',
    },
  ],
  swiggy_instamart: [],
  swiggy_food: [
    {
      platform: 'swiggy_food',
      key: 'chicken biryani',
      name: 'Chicken Biryani',
      frequency_rank_or_count: 4,
      avg_unit_price_paise: 32000,
      calorie_estimate: 950,
      redirect_url: 'https://swiggy.example/restaurant/biryani-house',
    },
  ],
}

const SUGGESTIONS_RESPONSE = {
  suggestions: [
    {
      platform: 'zepto',
      frequent_item: {
        key: 'variant-1',
        name: 'Amul Milk 1L',
        avg_unit_price_paise: 6500,
        calorie_estimate: null,
      },
      alternative: {
        name: 'Nandini Milk 1L',
        unit_price_paise: 5800,
        calorie_estimate: null,
        redirect_url: 'https://zepto.example/product/nandini-milk',
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

    expect(screen.getByRole('heading', { name: /usuals & recommendations/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/loading your usuals/i)).toBeInTheDocument()
    expect(getSuggestions).not.toHaveBeenCalled()
  })

  it('shows a retry option when usuals fail to load, and recovers on retry', async () => {
    getUsuals.mockRejectedValueOnce(new Error('network error'))

    renderRecommendations()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your usuals/i))

    getUsuals.mockResolvedValue(EMPTY_USUALS)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
  })

  it('renders the empty state and does not fetch suggestions when there are no usuals', async () => {
    getUsuals.mockResolvedValue(EMPTY_USUALS)

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/no usuals yet/i)).toBeInTheDocument())
    expect(screen.getByRole('link', { name: /connect an account/i })).toBeInTheDocument()
    expect(getSuggestions).not.toHaveBeenCalled()
  })

  it('renders usuals per platform with a reorder link to each item redirect_url', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockReturnValue(new Promise(() => {}))

    renderRecommendations()

    await waitFor(() => expect(screen.getByText('Amul Milk 1L')).toBeInTheDocument())
    expect(screen.getByText('Brown Bread')).toBeInTheDocument()
    expect(screen.getByText('Chicken Biryani')).toBeInTheDocument()

    // Instamart had no usuals at all, so its section is omitted entirely.
    expect(screen.queryByRole('heading', { name: /^instamart$/i })).not.toBeInTheDocument()

    const reorderLinks = screen.getAllByRole('link', { name: /^reorder$/i })
    expect(reorderLinks.map((link) => link.getAttribute('href'))).toEqual([
      'https://zepto.example/product/variant-1',
      'https://zepto.example/product/variant-2',
      'https://swiggy.example/restaurant/biryani-house',
    ])
    for (const link of reorderLinks) {
      expect(link).toHaveAttribute('target', '_blank')
    }
  })

  it('shows an inline suggestion only for the item it matches, with its own reorder link', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockResolvedValue(SUGGESTIONS_RESPONSE)

    renderRecommendations()

    await waitFor(() => expect(screen.getByText(/Nandini Milk 1L/)).toBeInTheDocument())
    expect(
      screen.getByRole('link', { name: /reorder alternative/i }),
    ).toHaveAttribute('href', 'https://zepto.example/product/nandini-milk')

    // Brown Bread and Chicken Biryani have no matching suggestion — no
    // broken/empty suggestion slot should render for them (AC-5).
    expect(screen.queryAllByLabelText(/suggested alternative/i)).toHaveLength(1)
  })

  it('shows a retry banner when suggestions fail to load, without affecting the usuals list', async () => {
    getUsuals.mockResolvedValue(POPULATED_USUALS)
    getSuggestions.mockRejectedValueOnce(new Error('boom'))

    renderRecommendations()

    await waitFor(() =>
      expect(screen.getByText(/couldn't load suggested alternatives/i)).toBeInTheDocument(),
    )
    expect(screen.getByText('Amul Milk 1L')).toBeInTheDocument()

    getSuggestions.mockResolvedValue(SUGGESTIONS_RESPONSE)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText(/Nandini Milk 1L/)).toBeInTheDocument())
  })
})
