import { useState, type FormEvent } from 'react'
import { askQuery, type QueryResponse } from '../lib/api'
import { formatPaise } from '../lib/format'

type Status = 'idle' | 'loading' | 'error' | 'answered'

export default function Query() {
  const [question, setQuestion] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [answer, setAnswer] = useState<QueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || status === 'loading') return

    setStatus('loading')
    setError(null)
    try {
      const response = await askQuery(trimmed)
      setAnswer(response)
      setStatus('answered')
    } catch {
      setError("Couldn't get an answer to that question. Please try again.")
      setStatus('error')
    }
  }

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Ask Overdulge</span>
        <h1>Ask a question</h1>
        <p className="muted">
          Ask a free-text question about your spending, like "how much did I spend on milk since
          May?"
        </p>
      </div>

      <form className="card card--pad-lg" onSubmit={handleSubmit}>
        <label htmlFor="query-question">Your question</label>
        <input
          id="query-question"
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="How much did I spend on Zepto last month?"
          disabled={status === 'loading'}
        />
        <button
          className="btn-primary"
          type="submit"
          disabled={status === 'loading' || !question.trim()}
        >
          {status === 'loading' ? 'Thinking…' : 'Ask'}
        </button>
      </form>

      <section aria-live="polite" aria-atomic="true">
        {status === 'loading' && (
          <div className="card card--pad-lg" aria-busy="true" aria-label="Answering your question">
            <div className="skeleton" />
          </div>
        )}

        {status === 'error' && error && (
          <div className="card card--pad-lg">
            <div role="alert">
              <p>{error}</p>
              <button type="button" onClick={() => setStatus('idle')}>
                Try again
              </button>
            </div>
          </div>
        )}

        {status === 'answered' && answer && !answer.has_data && (
          <div className="card card--pad-lg">
            <div className="empty">
              <span className="empty__icon" aria-hidden="true">
                🤔
              </span>
              <h2>I don't have enough data for that</h2>
              <p>{answer.explanation}</p>
            </div>
          </div>
        )}

        {status === 'answered' && answer && answer.has_data && (
          <div className="card card--pad-lg stat">
            {answer.amount_paise !== null && (
              <span className="stat__value">{formatPaise(answer.amount_paise)}</span>
            )}
            <p>{answer.explanation}</p>
          </div>
        )}
      </section>
    </main>
  )
}
