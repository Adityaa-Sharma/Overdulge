import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  ApiError,
  deleteBudget,
  getBudgetSuggestions,
  getBudgets,
  upsertBudget,
  type BudgetProgress,
  type BudgetSuggestion,
} from '../lib/api'
import { formatPaise } from '../lib/format'
import BudgetProgressBar from '../components/charts/BudgetProgressBar'

type LoadState = 'loading' | 'error' | 'ready'
type SuggestionsState = 'idle' | 'loading' | 'error' | 'ready'

const STATUS_LABEL: Record<BudgetProgress['status'], string> = {
  ok: 'On track',
  near: 'Near cap',
  over: 'Over cap',
}

const STATUS_BADGE_CLASS: Record<BudgetProgress['status'], string> = {
  ok: 'badge-good',
  near: 'badge-warn',
  over: 'badge-bad',
}

const SUGGESTION_STATUSES = new Set<BudgetProgress['status']>(['near', 'over'])

/** First of the current calendar month, matching `budgets.month`'s storage format. */
function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`
}

/** Parses a rupee-amount input (e.g. "1200" or "1200.50") into integer paise, or null if not a positive number. */
function parseRupeesToPaise(value: string): number | null {
  const rupees = Number(value)
  if (!Number.isFinite(rupees) || rupees <= 0) return null
  return Math.round(rupees * 100)
}

function paiseToRupeeInput(paise: number): string {
  return String(paise / 100)
}

export default function Budgets() {
  const [month] = useState(currentMonth)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [budgets, setBudgets] = useState<BudgetProgress[]>([])

  const [suggestionsState, setSuggestionsState] = useState<SuggestionsState>('idle')
  const [suggestions, setSuggestions] = useState<BudgetSuggestion[]>([])

  const [formError, setFormError] = useState<string | null>(null)
  const [overallInput, setOverallInput] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [newCategoryAmount, setNewCategoryAmount] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [savingKey, setSavingKey] = useState<string | null>(null)

  const fetchBudgets = useCallback(async () => {
    setLoadState('loading')
    try {
      const response = await getBudgets(month)
      setBudgets(response.budgets)
      setLoadState('ready')
      const overall = response.budgets.find((budget) => budget.category === null)
      setOverallInput(overall ? paiseToRupeeInput(overall.cap_paise) : '')
    } catch {
      setLoadState('error')
    }
  }, [month])

  useEffect(() => {
    fetchBudgets()
  }, [fetchBudgets])

  useEffect(() => {
    if (suggestionsState !== 'idle') return
    if (!budgets.some((budget) => SUGGESTION_STATUSES.has(budget.status))) return

    let cancelled = false
    setSuggestionsState('loading')
    getBudgetSuggestions(month)
      .then((response) => {
        if (cancelled) return
        setSuggestions(response.suggestions)
        setSuggestionsState('ready')
      })
      .catch(() => {
        if (cancelled) return
        setSuggestionsState('error')
      })
    return () => {
      cancelled = true
    }
  }, [budgets, month, suggestionsState])

  async function saveCap(category: string | null, amount: string, key: string) {
    const capPaise = parseRupeesToPaise(amount)
    if (capPaise === null) {
      setFormError('Enter a cap amount greater than ₹0.')
      return
    }
    setFormError(null)
    setSavingKey(key)
    try {
      await upsertBudget({ month, category, cap_paise: capPaise })
      await fetchBudgets()
      setNewCategory('')
      setNewCategoryAmount('')
      setEditingId(null)
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : 'Failed to save budget.')
    } finally {
      setSavingKey(null)
    }
  }

  async function handleDelete(id: string) {
    setFormError(null)
    setSavingKey(id)
    try {
      await deleteBudget(id)
      await fetchBudgets()
    } catch {
      setFormError('Failed to delete budget.')
    } finally {
      setSavingKey(null)
    }
  }

  function handleOverallSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    saveCap(null, overallInput, '__overall__')
  }

  function handleNewCategorySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const category = newCategory.trim()
    if (!category) {
      setFormError('Enter a category name.')
      return
    }
    saveCap(category, newCategoryAmount, '__new__')
  }

  function startEdit(budget: BudgetProgress) {
    setFormError(null)
    setEditingId(budget.id)
    setEditValue(paiseToRupeeInput(budget.cap_paise))
  }

  const overallBudget = budgets.find((budget) => budget.category === null) ?? null
  const categoryBudgets = budgets.filter(
    (budget): budget is BudgetProgress & { category: string } => budget.category !== null,
  )

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Stay on track</span>
        <h1>Budgets</h1>
      </div>

      {loadState === 'loading' && (
        <section aria-busy="true" aria-label="Loading budgets">
          <div className="grid">
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        </section>
      )}

      {loadState === 'error' && (
        <section className="card card--pad-lg">
          <div role="alert">
            <p>Couldn't load your budgets.</p>
            <button type="button" onClick={fetchBudgets}>
              Retry
            </button>
          </div>
        </section>
      )}

      {loadState === 'ready' && (
        <>
          {formError && <p role="alert">{formError}</p>}

          <section className="card card--pad-lg" aria-label="Overall monthly cap">
            <h2>Overall monthly cap</h2>

            {overallBudget && (
              <div className="stack">
                <div className="chart-breakdown__row">
                  <span>
                    {formatPaise(overallBudget.spent_paise)} of{' '}
                    {formatPaise(overallBudget.cap_paise)}
                  </span>
                  <span className={`badge ${STATUS_BADGE_CLASS[overallBudget.status]}`}>
                    {STATUS_LABEL[overallBudget.status]}
                  </span>
                </div>
                <BudgetProgressBar status={overallBudget.status} pct={overallBudget.pct} />
              </div>
            )}

            <form onSubmit={handleOverallSubmit}>
              <label htmlFor="overall-cap">Cap amount (₹)</label>
              <input
                id="overall-cap"
                type="number"
                min="0.01"
                step="0.01"
                value={overallInput}
                onChange={(event) => setOverallInput(event.target.value)}
                placeholder="e.g. 8000"
              />
              <div className="row">
                <button className="btn-primary" type="submit" disabled={savingKey === '__overall__'}>
                  {savingKey === '__overall__'
                    ? 'Saving…'
                    : overallBudget
                      ? 'Update overall cap'
                      : 'Set overall cap'}
                </button>
                {overallBudget && (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => handleDelete(overallBudget.id)}
                    disabled={savingKey === overallBudget.id}
                  >
                    {savingKey === overallBudget.id ? 'Removing…' : 'Remove overall cap'}
                  </button>
                )}
              </div>
            </form>
          </section>

          {budgets.length === 0 && (
            <section className="card card--pad-lg">
              <div className="empty">
                <span className="empty__icon" aria-hidden="true">
                  🎯
                </span>
                <h2>No caps set yet</h2>
                <p>
                  Set an overall monthly cap above, or add a per-category cap below, to start
                  tracking progress.
                </p>
              </div>
            </section>
          )}

          <section className="card card--pad-lg" aria-label="Category caps">
            <h2>Category caps</h2>

            {categoryBudgets.length === 0 ? (
              <p className="muted">No per-category caps yet.</p>
            ) : (
              <ul className="chart-breakdown__list">
                {categoryBudgets.map((budget) => (
                  <li key={budget.id}>
                    <div className="chart-breakdown__row">
                      <span>{budget.category}</span>
                      <span className={`badge ${STATUS_BADGE_CLASS[budget.status]}`}>
                        {STATUS_LABEL[budget.status]}
                      </span>
                    </div>
                    <BudgetProgressBar status={budget.status} pct={budget.pct} />
                    <div className="chart-breakdown__row">
                      <span>
                        {formatPaise(budget.spent_paise)} of {formatPaise(budget.cap_paise)}
                      </span>
                      <span>{Math.round(budget.pct * 100)}%</span>
                    </div>

                    {editingId === budget.id ? (
                      <div className="row">
                        <label htmlFor={`edit-cap-${budget.id}`} className="sr-only">
                          New cap amount for {budget.category}
                        </label>
                        <input
                          id={`edit-cap-${budget.id}`}
                          type="number"
                          min="0.01"
                          step="0.01"
                          value={editValue}
                          onChange={(event) => setEditValue(event.target.value)}
                        />
                        <button
                          type="button"
                          onClick={() => saveCap(budget.category, editValue, budget.id)}
                          disabled={savingKey === budget.id}
                        >
                          {savingKey === budget.id ? 'Saving…' : 'Save'}
                        </button>
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => setEditingId(null)}
                          disabled={savingKey === budget.id}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div className="row">
                        <button type="button" onClick={() => startEdit(budget)}>
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => handleDelete(budget.id)}
                          disabled={savingKey === budget.id}
                        >
                          {savingKey === budget.id ? 'Deleting…' : 'Delete'}
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}

            <form onSubmit={handleNewCategorySubmit} aria-label="Add a category cap">
              <label htmlFor="new-category-name">Category</label>
              <input
                id="new-category-name"
                type="text"
                value={newCategory}
                onChange={(event) => setNewCategory(event.target.value)}
                placeholder="e.g. groceries"
              />
              <label htmlFor="new-category-amount">Cap amount (₹)</label>
              <input
                id="new-category-amount"
                type="number"
                min="0.01"
                step="0.01"
                value={newCategoryAmount}
                onChange={(event) => setNewCategoryAmount(event.target.value)}
                placeholder="e.g. 3000"
              />
              <button className="btn-primary" type="submit" disabled={savingKey === '__new__'}>
                {savingKey === '__new__' ? 'Adding…' : 'Add category cap'}
              </button>
            </form>
          </section>

          {suggestionsState === 'loading' && (
            <section className="card card--pad-lg" aria-busy="true" aria-label="Loading suggestions">
              <div className="skeleton" />
            </section>
          )}

          {suggestionsState === 'error' && (
            <section className="card card--pad-lg">
              <div role="alert">
                <p>Couldn't load suggestions.</p>
                <button type="button" onClick={() => setSuggestionsState('idle')}>
                  Retry
                </button>
              </div>
            </section>
          )}

          {suggestionsState === 'ready' && suggestions.length > 0 && (
            <section className="card card--pad-lg" aria-label="Where to cut back">
              <h2>Where to cut back</h2>
              <ul className="link-list">
                {suggestions.map((suggestion) => (
                  <li key={suggestion.category ?? '__overall__'}>
                    <div>
                      <h3>{suggestion.category ?? 'Overall'}</h3>
                      <p>{suggestion.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  )
}
