import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Query from '../../src/routes/Query'

const { askQuery, ApiError } = vi.hoisted(() => {
  class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  }
  return { askQuery: vi.fn(), ApiError }
})

vi.mock('../../src/lib/api', () => ({ askQuery, ApiError }))

function renderQuery() {
  return render(
    <MemoryRouter>
      <Query />
    </MemoryRouter>,
  )
}

async function askQuestion(question: string) {
  fireEvent.change(screen.getByLabelText(/your question/i), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: /ask/i }))
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('Query', () => {
  it('disables submit until a question is typed', () => {
    renderQuery()

    expect(screen.getByRole('button', { name: /ask/i })).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/your question/i), {
      target: { value: 'how much did I spend on milk?' },
    })

    expect(screen.getByRole('button', { name: /ask/i })).toBeEnabled()
  })

  it('shows a loading state and disables submit while the request is in flight', async () => {
    askQuery.mockReturnValue(new Promise(() => {}))

    renderQuery()
    await askQuestion('how much did I spend on milk?')

    expect(screen.getByRole('button', { name: /thinking/i })).toBeDisabled()
    expect(screen.getByLabelText(/answering your question/i)).toBeInTheDocument()
  })

  it('renders the amount and explanation on a populated response', async () => {
    askQuery.mockResolvedValue({
      amount_paise: 123456,
      explanation: 'You spent this much on milk since May.',
      has_data: true,
    })

    renderQuery()
    await askQuestion('how much did I spend on milk since May?')

    await waitFor(() => expect(screen.getByText('₹1,234.56')).toBeInTheDocument())
    expect(screen.getByText('You spent this much on milk since May.')).toBeInTheDocument()
  })

  it('renders the not-enough-data message distinctly from a populated ₹0 answer', async () => {
    askQuery.mockResolvedValue({
      amount_paise: null,
      explanation: "I don't have enough synced data for that range.",
      has_data: false,
    })

    renderQuery()
    await askQuestion('how much did I spend last year?')

    await waitFor(() =>
      expect(screen.getByText(/i don't have enough data for that/i)).toBeInTheDocument(),
    )
    expect(screen.getByText("I don't have enough synced data for that range.")).toBeInTheDocument()
    expect(screen.queryByText('₹0.00')).not.toBeInTheDocument()
  })

  it('renders a true ₹0 answer as populated data, not the not-enough-data state', async () => {
    askQuery.mockResolvedValue({
      amount_paise: 0,
      explanation: 'You spent nothing on that in range.',
      has_data: true,
    })

    renderQuery()
    await askQuestion('how much did I spend on flights?')

    await waitFor(() => expect(screen.getByText('₹0.00')).toBeInTheDocument())
    expect(screen.queryByText(/i don't have enough data for that/i)).not.toBeInTheDocument()
  })

  it('shows an error state with a retry option on a mocked failure', async () => {
    askQuery.mockRejectedValue(new ApiError('boom', 502))

    renderQuery()
    await askQuestion('how much did I spend on milk?')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't get an answer/i),
    )
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })
})
