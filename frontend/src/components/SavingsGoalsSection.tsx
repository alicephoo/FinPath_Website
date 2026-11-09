import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useAuth } from '../auth/AuthContext'
import { createIdempotencyKey } from '../auth/api'
import { isApiError } from '../api/errors'
import {
  createSavingsContribution,
  createSavingsGoal,
  createSavingsWithdrawal,
  fetchSavingsEntries,
  fetchSavingsGoal,
  fetchSavingsGoals,
  updateSavingsGoal,
  type SavingsEntryResource,
  type SavingsEntryType,
  type SavingsGoalResource,
} from '../api/savings'
import { formatDateOnly, isValidDateOnly, localDateString } from '../data/dateOnly'
import {
  formatMoney,
  MAX_FINANCIAL_ENTRY_KYAT,
  MAX_MONEY_KYAT,
  moneyToEntryString,
  parseMoneyToKyat,
} from '../data/money'

type GoalTone = 'mint' | 'gold' | 'blue'

const goalTones: GoalTone[] = ['mint', 'gold', 'blue']

type GoalFormValues = {
  name: string
  target: string
  tone: GoalTone
}

type EntryFormValues = {
  amount: string
  date: string
  note: string
}

type HistoryState = {
  status: 'loading' | 'loaded' | 'error'
  entries: SavingsEntryResource[]
  error: string
}

const emptyGoalForm = (): GoalFormValues => ({
  name: '',
  target: '',
  tone: 'mint',
})

const emptyEntryForm = (): EntryFormValues => ({
  amount: '',
  date: localDateString(),
  note: '',
})

function normalizeTone(tone: string | null): GoalTone {
  return tone === 'gold' || tone === 'blue' ? tone : 'mint'
}

function formatGoalDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function goalProgress(goal: SavingsGoalResource): number {
  if (goal.target_kyat <= 0) return 0
  return Math.min(
    100,
    Math.round((goal.actual_balance_kyat / goal.target_kyat) * 100),
  )
}

function describeError(
  error: unknown,
  fallback: string,
  authMessage = 'Your session has expired. Sign in again to manage savings goals.',
): string {
  if (!isApiError(error)) return fallback
  if (error.status === 401) return authMessage
  if (error.fieldErrors.length > 0) {
    return error.fieldErrors.map((fieldError) => fieldError.message).join(' ')
  }
  return error.message || fallback
}

function SavingsGoalsSection() {
  const { user, authorizedRequest } = useAuth()
  const userId = user?.id ?? null

  const [goals, setGoals] = useState<SavingsGoalResource[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [goalFormOpen, setGoalFormOpen] = useState(false)
  const [goalForm, setGoalForm] = useState<GoalFormValues>(emptyGoalForm)
  const [editingGoalId, setEditingGoalId] = useState<string | null>(null)
  const [goalError, setGoalError] = useState('')
  const [goalNotice, setGoalNotice] = useState('')
  const [goalSubmitting, setGoalSubmitting] = useState(false)

  const [entryGoalId, setEntryGoalId] = useState<string | null>(null)
  const [entryMode, setEntryMode] = useState<SavingsEntryType>('contribution')
  const [entryForm, setEntryForm] = useState<EntryFormValues>(emptyEntryForm)
  const [entryError, setEntryError] = useState('')
  const [entrySubmitting, setEntrySubmitting] = useState(false)

  const [pageError, setPageError] = useState('')
  const [pageNotice, setPageNotice] = useState('')
  const [busyGoalId, setBusyGoalId] = useState<string | null>(null)
  const [history, setHistory] = useState<Record<string, HistoryState>>({})

  const mountedRef = useRef(true)
  const listRequestIdRef = useRef(0)
  const goalSubmittingRef = useRef(false)
  const entrySubmittingRef = useRef(false)
  const busyGoalRef = useRef<string | null>(null)
  const editEtagRef = useRef<string | null>(null)
  const createKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)
  const entryKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)
  const historyRequestIdsRef = useRef<Record<string, number>>({})
  const historyRef = useRef<Record<string, HistoryState>>({})

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadGoals = useCallback(async () => {
    const requestId = ++listRequestIdRef.current
    setListLoading(true)
    setListError('')

    try {
      const result = await authorizedRequest((accessToken) =>
        fetchSavingsGoals(accessToken),
      )
      if (!mountedRef.current || requestId !== listRequestIdRef.current) return
      setGoals(result)
    } catch (error) {
      if (!mountedRef.current || requestId !== listRequestIdRef.current) return
      setGoals([])
      setListError(
        describeError(
          error,
          'Unable to load savings goals. Please try again.',
          'Your session has expired. Sign in again to view savings goals.',
        ),
      )
    } finally {
      if (mountedRef.current && requestId === listRequestIdRef.current) {
        setListLoading(false)
      }
    }
  }, [authorizedRequest])

  useEffect(() => {
    if (!userId) {
      setListLoading(false)
      setListError('Your session is not available. Sign in again.')
      return
    }
    void loadGoals()
  }, [userId, loadGoals])

  const loadHistory = useCallback(async (goalId: string) => {
    const requestId = (historyRequestIdsRef.current[goalId] ?? 0) + 1
    historyRequestIdsRef.current[goalId] = requestId
    const loadingState: HistoryState = {
      status: 'loading',
      entries: historyRef.current[goalId]?.entries ?? [],
      error: '',
    }
    historyRef.current = { ...historyRef.current, [goalId]: loadingState }
    setHistory(historyRef.current)

    try {
      const entries = await authorizedRequest((accessToken) =>
        fetchSavingsEntries(accessToken, goalId),
      )
      if (
        !mountedRef.current
        || historyRequestIdsRef.current[goalId] !== requestId
      ) {
        return
      }
      historyRef.current = {
        ...historyRef.current,
        [goalId]: {
          status: 'loaded',
          entries,
          error: '',
        },
      }
      setHistory(historyRef.current)
    } catch (error) {
      if (
        !mountedRef.current
        || historyRequestIdsRef.current[goalId] !== requestId
      ) {
        return
      }
      historyRef.current = {
        ...historyRef.current,
        [goalId]: {
          status: 'error',
          entries: [],
          error: describeError(
            error,
            'Unable to load contribution history. Please try again.',
            'Your session has expired. Sign in again to view contributions.',
          ),
        },
      }
      setHistory(historyRef.current)
    }
  }, [authorizedRequest])

  function closeEntryForm() {
    setEntryGoalId(null)
    setEntryMode('contribution')
    setEntryForm(emptyEntryForm())
    setEntryError('')
    entryKeyRef.current = null
  }

  function closeGoalForm() {
    setGoalForm(emptyGoalForm())
    setEditingGoalId(null)
    setGoalError('')
    setGoalNotice('')
    setGoalFormOpen(false)
    editEtagRef.current = null
    createKeyRef.current = null
  }

  function applyGoalToForm(goal: SavingsGoalResource) {
    setGoalForm({
      name: goal.name,
      target: moneyToEntryString(goal.target_kyat),
      tone: normalizeTone(goal.tone),
    })
  }

  function beginAdd() {
    if (goalSubmittingRef.current || entrySubmittingRef.current) return
    closeEntryForm()
    setGoalForm(emptyGoalForm())
    setEditingGoalId(null)
    setGoalError('')
    setGoalNotice('')
    setPageError('')
    setPageNotice('')
    editEtagRef.current = null
    createKeyRef.current = null
    setGoalFormOpen(true)
  }

  async function beginEdit(goal: SavingsGoalResource) {
    if (
      goalSubmittingRef.current
      || entrySubmittingRef.current
      || busyGoalRef.current !== null
    ) {
      return
    }

    busyGoalRef.current = goal.id
    setBusyGoalId(goal.id)
    setGoalError('')
    setGoalNotice('')
    setPageError('')
    setPageNotice('')
    closeEntryForm()

    try {
      const view = await authorizedRequest((accessToken) =>
        fetchSavingsGoal(accessToken, goal.id),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setPageError(
          'Unable to edit this goal because the server did not provide a revision. Reload the page and try again.',
        )
        return
      }

      editEtagRef.current = view.etag
      applyGoalToForm(view.goal)
      setGoals((current) => current.map((item) =>
        item.id === view.goal.id ? view.goal : item,
      ))
      setEditingGoalId(view.goal.id)
      setGoalFormOpen(true)
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setPageError('This savings goal no longer exists. The list is being refreshed.')
        void loadGoals()
        return
      }
      setPageError(
        describeError(
          error,
          'Unable to open this goal for editing. Please try again.',
          'Your session has expired. Sign in again to edit savings goals.',
        ),
      )
    } finally {
      busyGoalRef.current = null
      if (mountedRef.current) setBusyGoalId(null)
    }
  }

  async function reloadLatestGoalForEdit(goalId: string) {
    try {
      const view = await authorizedRequest((accessToken) =>
        fetchSavingsGoal(accessToken, goalId),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setGoalError(
          'The latest revision was not provided. Close the form and reopen the goal.',
        )
        return
      }
      editEtagRef.current = view.etag
      applyGoalToForm(view.goal)
      setEditingGoalId(view.goal.id)
      setGoals((current) => current.map((item) =>
        item.id === view.goal.id ? view.goal : item,
      ))
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setGoalError('This savings goal no longer exists.')
        setGoalFormOpen(false)
        setEditingGoalId(null)
        void loadGoals()
        return
      }
      setGoalError(
        describeError(
          error,
          'Unable to reload the latest goal. Close the form and try again.',
          'Your session has expired. Sign in again to edit savings goals.',
        ),
      )
    }
  }

  async function saveGoal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (goalSubmittingRef.current) return

    setGoalError('')
    setGoalNotice('')
    setPageError('')
    setPageNotice('')

    const name = goalForm.name.trim()
    const targetKyat = parseMoneyToKyat(goalForm.target, {
      maxAmount: MAX_MONEY_KYAT,
    })

    if (!name) {
      setGoalError('Enter a name for this savings goal.')
      return
    }
    if (name.length > 80) {
      setGoalError('Savings goal name must be 80 characters or fewer.')
      return
    }
    if (targetKyat === null) {
      setGoalError(
        `Enter a positive whole-Kyat target up to ${formatMoney(MAX_MONEY_KYAT)}.`,
      )
      return
    }

    const input = {
      name,
      target_kyat: targetKyat,
      tone: goalForm.tone,
    }

    goalSubmittingRef.current = true
    setGoalSubmitting(true)

    try {
      if (editingGoalId) {
        const etag = editEtagRef.current
        if (!etag) {
          setGoalError(
            'The current goal revision is missing. Close the form and reopen the goal.',
          )
          return
        }

        const updatedGoalId = editingGoalId
        await authorizedRequest((accessToken) =>
          updateSavingsGoal(accessToken, updatedGoalId, input, etag),
        )
        if (!mountedRef.current) return

        closeGoalForm()
        setPageNotice('Savings goal updated.')
        void loadGoals()
        void loadHistory(updatedGoalId)
        return
      }

      const fingerprint = JSON.stringify(input)
      if (
        !createKeyRef.current
        || createKeyRef.current.fingerprint !== fingerprint
      ) {
        createKeyRef.current = {
          fingerprint,
          key: createIdempotencyKey(),
        }
      }

      await authorizedRequest((accessToken) =>
        createSavingsGoal(
          accessToken,
          input,
          createKeyRef.current?.key ?? createIdempotencyKey(),
        ),
      )
      if (!mountedRef.current) return

      createKeyRef.current = null
      closeGoalForm()
      setPageNotice('Savings goal added.')
      void loadGoals()
    } catch (error) {
      if (!mountedRef.current) return

      if (
        editingGoalId
        && isApiError(error)
        && (error.status === 412 || error.status === 428)
      ) {
        setGoalNotice(
          'This goal was changed elsewhere. The latest record has been reloaded; review it and save again.',
        )
        await reloadLatestGoalForEdit(editingGoalId)
        return
      }

      setGoalError(
        describeError(
          error,
          editingGoalId
            ? 'Unable to save this savings goal. Please try again.'
            : 'Unable to add this savings goal. Please try again.',
          'Your session has expired. Sign in again to save savings goals.',
        ),
      )
    } finally {
      goalSubmittingRef.current = false
      if (mountedRef.current) setGoalSubmitting(false)
    }
  }

  function openEntryForm(
    goal: SavingsGoalResource,
    mode: SavingsEntryType = 'contribution',
  ) {
    if (
      goalSubmittingRef.current
      || entrySubmittingRef.current
      || busyGoalRef.current !== null
      || goal.status !== 'active'
    ) {
      return
    }
    closeGoalForm()
    setEntryGoalId(goal.id)
    setEntryMode(mode)
    setEntryForm(emptyEntryForm())
    setEntryError('')
    setPageError('')
    setPageNotice('')
    entryKeyRef.current = null
  }

  async function saveEntry(
    event: FormEvent<HTMLFormElement>,
    goal: SavingsGoalResource,
  ) {
    event.preventDefault()
    if (entrySubmittingRef.current) return

    setEntryError('')
    setPageError('')
    setPageNotice('')

    const amountKyat = parseMoneyToKyat(entryForm.amount, {
      maxAmount: MAX_FINANCIAL_ENTRY_KYAT,
    })
    const note = entryForm.note.trim()
    const isWithdrawal = entryMode === 'withdrawal'
    const entryLabel = isWithdrawal ? 'withdrawal' : 'contribution'

    if (amountKyat === null) {
      setEntryError(
        `Enter a positive whole-Kyat ${entryLabel} up to ${formatMoney(MAX_FINANCIAL_ENTRY_KYAT)}.`,
      )
      return
    }
    if (!isValidDateOnly(entryForm.date)) {
      setEntryError(`Choose a valid ${entryLabel} date.`)
      return
    }
    if (note.length > 100) {
      setEntryError(
        `${isWithdrawal ? 'Withdrawal' : 'Contribution'} note must be 100 characters or fewer.`,
      )
      return
    }
    if (isWithdrawal && amountKyat > goal.actual_balance_kyat) {
      setEntryError(
        `Withdrawal cannot exceed the available savings balance of ${formatMoney(goal.actual_balance_kyat)}.`,
      )
      return
    }

    const input = {
      amount_kyat: amountKyat,
      entry_date: entryForm.date,
      note: note || null,
    }

    entrySubmittingRef.current = true
    setEntrySubmitting(true)

    try {
      const fingerprint = JSON.stringify({
        goalId: goal.id,
        entryType: entryMode,
        ...input,
      })
      if (
        !entryKeyRef.current
        || entryKeyRef.current.fingerprint !== fingerprint
      ) {
        entryKeyRef.current = {
          fingerprint,
          key: createIdempotencyKey(),
        }
      }

      await authorizedRequest((accessToken) =>
        isWithdrawal
          ? createSavingsWithdrawal(
            accessToken,
            goal.id,
            input,
            entryKeyRef.current?.key ?? createIdempotencyKey(),
          )
          : createSavingsContribution(
            accessToken,
            goal.id,
            input,
            entryKeyRef.current?.key ?? createIdempotencyKey(),
          ),
      )
      if (!mountedRef.current) return

      entryKeyRef.current = null
      closeEntryForm()
      setPageNotice(
        isWithdrawal ? 'Withdrawal recorded.' : 'Contribution recorded.',
      )
      void loadGoals()
      void loadHistory(goal.id)
    } catch (error) {
      if (!mountedRef.current) return
      setEntryError(
        describeError(
          error,
          `Unable to record this ${entryLabel}. Please try again.`,
          'Your session has expired. Sign in again to manage savings entries.',
        ),
      )
    } finally {
      entrySubmittingRef.current = false
      if (mountedRef.current) setEntrySubmitting(false)
    }
  }

  const busy = goalSubmitting || entrySubmitting || busyGoalId !== null

  return (
    <section className="panel savings-management" aria-labelledby="savings-management-title">
      <div className="management-heading">
        <div>
          <p className="panel-overline">LIVE RECORDS · BACKEND</p>
          <h2 id="savings-management-title">Savings Goals</h2>
          <p className="management-description">
            Set a target and track contributions and withdrawals. Actual
            balance, remaining amount, and progress are calculated by FinPath.
          </p>
        </div>
        <div className="savings-heading-actions">
          <button
            className="primary-button"
            type="button"
            onClick={beginAdd}
            disabled={busy}
          >
            <span aria-hidden="true">+</span> Add goal
          </button>
        </div>
      </div>

      <p className="savings-plan-form-note">
        Corrections, archive/restore, and daily savings plans are not available
        in this Phase 1 backend integration.
      </p>

      {goalFormOpen && (
        <form className="savings-goal-form" noValidate onSubmit={saveGoal} aria-labelledby="savings-goal-form-title">
          <div className="form-heading">
            <h3 id="savings-goal-form-title">
              {editingGoalId ? 'Edit savings goal' : 'Add savings goal'}
            </h3>
            <span>{editingGoalId ? 'Live record' : 'New record'}</span>
          </div>
          {goalError && <p className="form-error" role="alert">{goalError}</p>}
          {goalNotice && <p className="form-notice" role="status">{goalNotice}</p>}
          <div className="savings-goal-fields">
            <label>
              <span>Goal name <b aria-hidden="true">*</b></span>
              <input
                autoFocus
                required
                maxLength={80}
                disabled={goalSubmitting}
                value={goalForm.name}
                onChange={(event) => setGoalForm({
                  ...goalForm,
                  name: event.target.value,
                })}
                placeholder="e.g. Emergency fund"
              />
            </label>
            <label>
              <span>Target amount (MMK) <b aria-hidden="true">*</b></span>
              <input
                required
                type="number"
                min="1"
                max={MAX_MONEY_KYAT}
                step="1"
                inputMode="numeric"
                disabled={goalSubmitting}
                value={goalForm.target}
                onChange={(event) => setGoalForm({
                  ...goalForm,
                  target: event.target.value,
                })}
                placeholder="e.g. 12000000"
              />
            </label>
            <label>
              <span>Tone</span>
              <select
                disabled={goalSubmitting}
                value={goalForm.tone}
                onChange={(event) => setGoalForm({
                  ...goalForm,
                  tone: normalizeTone(event.target.value),
                })}
              >
                {goalTones.map((tone) => (
                  <option key={tone} value={tone}>
                    {tone.charAt(0).toUpperCase() + tone.slice(1)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="form-actions">
            <button
              className="text-button"
              type="button"
              onClick={closeGoalForm}
              disabled={goalSubmitting}
            >
              Cancel
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={goalSubmitting}
            >
              {goalSubmitting
                ? 'Saving…'
                : editingGoalId ? 'Save goal' : 'Add goal'}
            </button>
          </div>
        </form>
      )}

      {pageError && <p className="form-error" role="alert">{pageError}</p>}
      {pageNotice && <p className="form-notice" role="status">{pageNotice}</p>}

      {listLoading ? (
        <div className="empty-transactions savings-empty" role="status">
          <strong>Loading savings goals…</strong>
          <span>Fetching your savings records from FinPath.</span>
        </div>
      ) : listError ? (
        <div className="empty-transactions savings-empty" role="alert">
          <strong>Unable to load savings goals</strong>
          <span>{listError}</span>
          <button
            className="text-button"
            type="button"
            onClick={() => { void loadGoals() }}
          >
            Retry
          </button>
        </div>
      ) : goals.length === 0 ? (
        <div className="empty-transactions savings-empty" role="status">
          <strong>No savings goals yet</strong>
          <span>Add a goal to start tracking contributions and progress.</span>
        </div>
      ) : (
        <div className="savings-goal-list">
          {goals.map((goal) => {
            const tone = normalizeTone(goal.tone)
            const progress = goalProgress(goal)
            const goalHistory = history[goal.id]
            const isArchived = goal.status === 'archived'
            const isTargetReached = goal.remaining_kyat === 0
            return (
              <article className="savings-goal-card" key={goal.id} aria-labelledby={`savings-goal-${goal.id}`}>
                <div className="savings-goal-card-heading">
                  <div>
                    <p className="panel-overline">
                      SAVINGS GOAL · {isArchived ? 'ARCHIVED' : 'ACTIVE'}
                    </p>
                    <h3 id={`savings-goal-${goal.id}`}>{goal.name}</h3>
                  </div>
                  <div className="savings-goal-actions">
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => { void beginEdit(goal) }}
                      disabled={busy}
                    >
                      {busyGoalId === goal.id ? 'Opening…' : 'Edit'}
                    </button>
                  </div>
                </div>

                <div className="savings-goal-metrics">
                  <div>
                    <span>Actually saved</span>
                    <strong>{formatMoney(goal.actual_balance_kyat)}</strong>
                  </div>
                  <div>
                    <span>Target</span>
                    <strong>{formatMoney(goal.target_kyat)}</strong>
                  </div>
                  <div>
                    <span>Target remaining</span>
                    <strong>{formatMoney(goal.remaining_kyat)}</strong>
                  </div>
                </div>
                <div
                  className="savings-progress-track"
                  role="progressbar"
                  aria-label={`${goal.name} progress`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={progress}
                >
                  <span className={`progress-fill ${tone}`} style={{ width: `${progress}%` }} />
                </div>
                <p className="savings-progress-caption">
                  {progress}% complete{isTargetReached ? ' · Target reached' : ''}
                </p>

                {!isArchived && (
                  <div className="savings-entry-actions">
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() => openEntryForm(goal)}
                      disabled={busy}
                    >
                      Add contribution
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => openEntryForm(goal, 'withdrawal')}
                      disabled={busy || goal.actual_balance_kyat <= 0}
                      title={
                        goal.actual_balance_kyat <= 0
                          ? 'No savings balance is available to withdraw.'
                          : undefined
                      }
                    >
                      Withdraw
                    </button>
                  </div>
                )}

                {entryGoalId === goal.id && (
                  <form
                    className="savings-entry-form"
                    noValidate
                    onSubmit={(event) => { void saveEntry(event, goal) }}
                    aria-labelledby={`savings-entry-form-${goal.id}`}
                  >
                    <div className="form-heading">
                      <h4 id={`savings-entry-form-${goal.id}`}>
                        {entryMode === 'withdrawal'
                          ? 'Withdraw from savings'
                          : 'Add contribution'}
                      </h4>
                      <span>
                        {entryMode === 'withdrawal'
                          ? `Available ${formatMoney(goal.actual_balance_kyat)}`
                          : `Up to ${formatMoney(MAX_FINANCIAL_ENTRY_KYAT)} per entry`}
                      </span>
                    </div>
                    {entryError && <p className="form-error" role="alert">{entryError}</p>}
                    <div className="savings-entry-fields">
                      <label>
                        <span>Amount (MMK) <b aria-hidden="true">*</b></span>
                        <input
                          autoFocus
                          required
                          type="number"
                          min="1"
                          max={
                            entryMode === 'withdrawal'
                              ? Math.min(
                                MAX_FINANCIAL_ENTRY_KYAT,
                                Math.max(1, goal.actual_balance_kyat),
                              )
                              : MAX_FINANCIAL_ENTRY_KYAT
                          }
                          step="1"
                          inputMode="numeric"
                          disabled={entrySubmitting}
                          value={entryForm.amount}
                          onChange={(event) => setEntryForm({
                            ...entryForm,
                            amount: event.target.value,
                          })}
                          placeholder="e.g. 100000"
                        />
                      </label>
                      <label>
                        <span>Date <b aria-hidden="true">*</b></span>
                        <input
                          required
                          type="date"
                          disabled={entrySubmitting}
                          value={entryForm.date}
                          onChange={(event) => setEntryForm({
                            ...entryForm,
                            date: event.target.value,
                          })}
                        />
                      </label>
                      <label>
                        <span>Note <small>(optional)</small></span>
                        <input
                          maxLength={100}
                          disabled={entrySubmitting}
                          value={entryForm.note}
                          onChange={(event) => setEntryForm({
                            ...entryForm,
                            note: event.target.value,
                          })}
                          placeholder={
                            entryMode === 'withdrawal'
                              ? 'e.g. Emergency expense'
                              : 'e.g. Monthly saving'
                          }
                        />
                      </label>
                    </div>
                    <div className="form-actions">
                      <button
                        className="text-button"
                        type="button"
                        onClick={closeEntryForm}
                        disabled={entrySubmitting}
                      >
                        Cancel
                      </button>
                      <button
                        className="primary-button"
                        type="submit"
                        disabled={entrySubmitting}
                      >
                        {entrySubmitting
                          ? 'Saving…'
                          : entryMode === 'withdrawal'
                            ? 'Save withdrawal'
                            : 'Save contribution'}
                      </button>
                    </div>
                  </form>
                )}

                <details
                  className="savings-history"
                  onToggle={(event) => {
                    if (event.currentTarget.open) void loadHistory(goal.id)
                  }}
                >
                  <summary>
                    Savings history{' '}
                    <span>
                      {goalHistory?.status === 'loaded'
                        ? `${goalHistory.entries.length} ${goalHistory.entries.length === 1 ? 'entry' : 'entries'}`
                        : goalHistory?.status === 'loading'
                          ? 'Loading…'
                          : 'View'}
                    </span>
                  </summary>
                  {goalHistory?.status === 'loading' && (
                    <p className="history-empty" role="status">Loading savings entries…</p>
                  )}
                  {goalHistory?.status === 'error' && (
                    <p className="history-empty" role="alert">
                      {goalHistory.error}{' '}
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => { void loadHistory(goal.id) }}
                      >
                        Retry history
                      </button>
                    </p>
                  )}
                  {goalHistory?.status === 'loaded' && (
                    goalHistory.entries.length > 0 ? (
                      <ul>
                        {goalHistory.entries.map((entry) => {
                          const isContribution = entry.entry_type === 'contribution'
                          const label = entry.correction_of_entry_id
                            ? `Correction · ${entry.correction_reason || 'Reason not provided'}`
                            : isContribution ? 'Contribution' : 'Withdrawal'
                          return (
                            <li key={entry.id}>
                              <span>
                                <strong>{entry.note || label}</strong>
                                <small>
                                  {label} · {formatGoalDate(entry.entry_date)}
                                </small>
                              </span>
                              <strong className={entry.entry_type}>
                                {isContribution ? '+' : '−'}{formatMoney(entry.amount_kyat)}
                              </strong>
                            </li>
                          )
                        })}
                      </ul>
                    ) : <p className="history-empty">No contributions recorded yet.</p>
                  )}
                </details>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

export default SavingsGoalsSection
