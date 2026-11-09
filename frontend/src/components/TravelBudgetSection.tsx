import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from 'react'
import { useAuth } from '../auth/AuthContext'
import { createIdempotencyKey } from '../auth/api'
import { isApiError } from '../api/errors'
import {
  completeTrip as completeTripRequest,
  createTrip as createTripRequest,
  createTripBudget,
  createTripSpending,
  deleteTrip as deleteTripRequest,
  deleteTripBudget,
  deleteTripSpending,
  fetchTrip,
  fetchTripBudget,
  fetchTripBudgets,
  fetchTripSpending,
  fetchTripSpendingEntry,
  fetchTrips,
  importTrips,
  parseTripImportDocument,
  updateTrip as updateTripRequest,
  updateTripBudget,
  type TripBudgetResource,
  type TripResource,
  type TripSpendingResource,
} from '../api/travel'
import {
  formatTripKyat,
  formatTripDate,
  getBudgetProgressPercent,
  getCategorySpendingKyat,
  getTripBudgetKyat,
  getTripSpendingKyat,
  type Trip,
  type TripCategoryBudget,
  type TripSpendingEntry,
} from '../data/travelBudgetMockData'
import { isValidDateOnly, localDateString } from '../data/dateOnly'
import {
  MAX_FINANCIAL_ENTRY_KYAT,
  moneyToEntryString,
  parseMoneyToKyat,
} from '../data/money'

type TripFormValues = {
  name: string
  destination: string
  startDate: string
  endDate: string
}

type BudgetFormValues = {
  category: string
  amount: string
}

type SpendingFormValues = {
  description: string
  amount: string
  date: string
}

type SubmittingAction =
  | 'trip'
  | 'budget'
  | 'spending'
  | 'complete-trip'
  | 'delete-trip'
  | 'delete-budget'
  | 'delete-spending'
  | 'import'
  | null

const emptyTripForm: TripFormValues = { name: '', destination: '', startDate: '', endDate: '' }
const emptyBudgetForm: BudgetFormValues = { category: '', amount: '' }

function describeError(error: unknown, fallback: string): string {
  if (!isApiError(error)) {
    return error instanceof Error && error.message ? error.message : fallback
  }
  if (error.status === 401) {
    return 'Your session has expired. Sign in again to manage travel records.'
  }
  if (error.status === 412) {
    return 'This record changed in another session. Reload the trip and try again.'
  }
  return error.message || fallback
}

function mapTrip(
  trip: TripResource,
  budgets: TripBudgetResource[],
  spending: TripSpendingResource[],
): Trip {
  return {
    id: trip.id,
    name: trip.name,
    destination: trip.destination,
    startDate: trip.start_date,
    endDate: trip.end_date,
    status: trip.status,
    budgets: budgets.map((budget) => ({
      id: budget.id,
      category: budget.category,
      budgetKyat: budget.budget_kyat,
    })),
    spending: spending.map((entry) => ({
      id: entry.id,
      categoryBudgetId: entry.category_budget_id,
      category: entry.category,
      description: entry.description,
      amountKyat: entry.amount_kyat,
      date: entry.spending_date,
    })),
  }
}

function requiredEtag(etag: string | null): string {
  if (!etag) {
    throw new Error('The server did not return a record revision. Reload and try again.')
  }
  return etag
}

function importSummary(result: {
  trips_created: number
  trips_skipped_duplicates: number
  budgets_created: number
  budgets_skipped_duplicates: number
  spending_created: number
  spending_skipped_duplicates: number
}): string {
  return [
    `Imported ${result.trips_created} ${result.trips_created === 1 ? 'trip' : 'trips'}`,
    `${result.budgets_created} planned ${result.budgets_created === 1 ? 'expense' : 'expenses'}`,
    `${result.spending_created} spending ${result.spending_created === 1 ? 'entry' : 'entries'}`,
    `${result.trips_skipped_duplicates + result.budgets_skipped_duplicates + result.spending_skipped_duplicates} duplicate rows skipped`,
  ].join('; ') + '.'
}

function TravelBudgetSection() {
  const { user, authorizedRequest } = useAuth()
  const [trips, setTrips] = useState<Trip[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [pageError, setPageError] = useState('')
  const [pageNotice, setPageNotice] = useState('')
  const [submitting, setSubmitting] = useState<SubmittingAction>(null)
  const [tripFormOpen, setTripFormOpen] = useState(false)
  const [editingTripId, setEditingTripId] = useState<string | null>(null)
  const [tripForm, setTripForm] = useState<TripFormValues>(emptyTripForm)
  const [tripError, setTripError] = useState('')
  const [budgetForm, setBudgetForm] = useState<BudgetFormValues | null>(null)
  const [editingBudgetId, setEditingBudgetId] = useState<string | null>(null)
  const [budgetTripId, setBudgetTripId] = useState<string | null>(null)
  const [budgetError, setBudgetError] = useState('')
  const [spendingForm, setSpendingForm] = useState<SpendingFormValues | null>(null)
  const [spendingBudgetId, setSpendingBudgetId] = useState<string | null>(null)
  const [spendingTripId, setSpendingTripId] = useState<string | null>(null)
  const [spendingError, setSpendingError] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importError, setImportError] = useState('')
  const [importNotice, setImportNotice] = useState('')
  const mountedRef = useRef(true)
  const requestIdRef = useRef(0)
  const inFlightRef = useRef(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const loadTrips = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setLoadError('')
    try {
      const records = await authorizedRequest((accessToken) => fetchTrips(accessToken))
      const mapped = await Promise.all(
        records.map(async (record) => {
          const [budgets, spending] = await Promise.all([
            authorizedRequest((accessToken) => fetchTripBudgets(accessToken, record.id)),
            authorizedRequest((accessToken) => fetchTripSpending(accessToken, record.id)),
          ])
          return mapTrip(record, budgets, spending)
        }),
      )
      if (!mountedRef.current || requestId !== requestIdRef.current) return
      setTrips(mapped)
    } catch (error) {
      if (!mountedRef.current || requestId !== requestIdRef.current) return
      setLoadError(describeError(
        error,
        'Unable to load your travel records. Please try again.',
      ))
    } finally {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [authorizedRequest])

  useEffect(() => {
    mountedRef.current = true
    void loadTrips()
    return () => {
      mountedRef.current = false
    }
  }, [loadTrips, user?.id])

  function beginAction(action: Exclude<SubmittingAction, null>): boolean {
    if (inFlightRef.current) return false
    inFlightRef.current = true
    setSubmitting(action)
    setPageError('')
    setPageNotice('')
    return true
  }

  function endAction() {
    inFlightRef.current = false
    setSubmitting(null)
  }

  function closeTripForm() {
    setTripForm(emptyTripForm)
    setEditingTripId(null)
    setTripError('')
    setTripFormOpen(false)
  }

  function beginAddTrip() {
    closeTripForm()
    setBudgetForm(null)
    setSpendingForm(null)
    setTripFormOpen(true)
  }

  function beginEditTrip(trip: Trip) {
    setBudgetForm(null)
    setSpendingForm(null)
    setTripForm({
      name: trip.name,
      destination: trip.destination,
      startDate: trip.startDate,
      endDate: trip.endDate,
    })
    setEditingTripId(trip.id)
    setTripError('')
    setTripFormOpen(true)
  }

  async function saveTrip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = tripForm.name.trim()
    const destination = tripForm.destination.trim()
    if (!name || !destination) {
      setTripError('Enter a trip name and destination.')
      return
    }
    if (!isValidDateOnly(tripForm.startDate) || !isValidDateOnly(tripForm.endDate)) {
      setTripError('Enter valid start and end dates.')
      return
    }
    if (tripForm.endDate < tripForm.startDate) {
      setTripError('The return date cannot be before the departure date.')
      return
    }
    if (!beginAction('trip')) return

    try {
      const key = createIdempotencyKey()
      if (editingTripId) {
        const current = await authorizedRequest((accessToken) =>
          fetchTrip(accessToken, editingTripId))
        await authorizedRequest((accessToken) => updateTripRequest(
          accessToken,
          editingTripId,
          { name, destination, start_date: tripForm.startDate, end_date: tripForm.endDate },
          requiredEtag(current.etag),
        ))
        setPageNotice('Trip updated.')
      } else {
        await authorizedRequest((accessToken) => createTripRequest(
          accessToken,
          { name, destination, start_date: tripForm.startDate, end_date: tripForm.endDate },
          key,
        ))
        setPageNotice('Trip added.')
      }
      closeTripForm()
      await loadTrips()
    } catch (error) {
      setTripError(describeError(error, 'Unable to save this trip. Please try again.'))
    } finally {
      endAction()
    }
  }

  async function completeTrip(trip: Trip) {
    const totalBudgetKyat = getTripBudgetKyat(trip)
    const totalSpentKyat = getTripSpendingKyat(trip)
    const unusedKyat = Math.max(0, totalBudgetKyat - totalSpentKyat)
    const released = unusedKyat > 0
      ? `${formatTripKyat(unusedKyat)} of unused budget will be returned to your available balance.`
      : 'There is no unused budget to return.'
    const confirmed = window.confirm(
      `Complete “${trip.name}”? ${released} Actual spending of ${formatTripKyat(totalSpentKyat)} will remain recorded.`,
    )
    if (!confirmed) return
    if (!beginAction('complete-trip')) return

    try {
      const current = await authorizedRequest((accessToken) => fetchTrip(accessToken, trip.id))
      await authorizedRequest((accessToken) => completeTripRequest(
        accessToken,
        trip.id,
        requiredEtag(current.etag),
        createIdempotencyKey(),
      ))
      setPageNotice(
        unusedKyat > 0
          ? `Trip completed. ${formatTripKyat(unusedKyat)} of unused budget was returned to your available balance.`
          : 'Trip completed. Actual spending remains recorded.',
      )
      await loadTrips()
    } catch (error) {
      setPageError(describeError(error, 'Unable to complete this trip. Please try again.'))
    } finally {
      endAction()
    }
  }

  async function deleteTrip(trip: Trip) {
    const totalBudgetKyat = getTripBudgetKyat(trip)
    const totalSpentKyat = getTripSpendingKyat(trip)
    const unusedKyat = Math.max(0, totalBudgetKyat - totalSpentKyat)
    const released = unusedKyat > 0
      ? ` ${formatTripKyat(unusedKyat)} of unused budget will be released.`
      : ''
    const confirmed = window.confirm(
      `Delete “${trip.name}”?${released} Recorded spending of ${formatTripKyat(totalSpentKyat)} will remain in your financial totals.`,
    )
    if (!confirmed) return
    if (!beginAction('delete-trip')) return

    try {
      const current = await authorizedRequest((accessToken) => fetchTrip(accessToken, trip.id))
      await authorizedRequest((accessToken) => deleteTripRequest(
        accessToken,
        trip.id,
        requiredEtag(current.etag),
        createIdempotencyKey(),
      ))
      if (editingTripId === trip.id) closeTripForm()
      if (budgetTripId === trip.id) setBudgetForm(null)
      if (spendingTripId === trip.id) setSpendingForm(null)
      setPageNotice('Trip deleted. Unused budget was released and actual spending remains recorded.')
      await loadTrips()
    } catch (error) {
      setPageError(describeError(error, 'Unable to delete this trip. Please try again.'))
    } finally {
      endAction()
    }
  }

  function beginAddBudget(trip: Trip) {
    closeTripForm()
    setSpendingForm(null)
    setBudgetTripId(trip.id)
    setEditingBudgetId(null)
    setBudgetForm(emptyBudgetForm)
    setBudgetError('')
  }

  function beginEditBudget(trip: Trip, budget: TripCategoryBudget) {
    closeTripForm()
    setSpendingForm(null)
    setBudgetTripId(trip.id)
    setEditingBudgetId(budget.id)
    setBudgetForm({ category: budget.category, amount: moneyToEntryString(budget.budgetKyat) })
    setBudgetError('')
  }

  async function saveBudget(event: FormEvent<HTMLFormElement>, trip: Trip) {
    event.preventDefault()
    if (!budgetForm) return
    const category = budgetForm.category.trim()
    const budgetKyat = parseMoneyToKyat(budgetForm.amount, { allowZero: true })

    if (!category) {
      setBudgetError('Enter a category name.')
      return
    }
    if (budgetKyat === null) {
      setBudgetError('Enter a valid budget amount within the supported range.')
      return
    }
    if (trip.budgets.some((budget) =>
      budget.id !== editingBudgetId
      && budget.category.toLocaleLowerCase() === category.toLocaleLowerCase())) {
      setBudgetError('This trip already has a budget for that category.')
      return
    }
    if (!beginAction('budget')) return

    try {
      if (editingBudgetId) {
        const current = await authorizedRequest((accessToken) =>
          fetchTripBudget(accessToken, trip.id, editingBudgetId))
        await authorizedRequest((accessToken) => updateTripBudget(
          accessToken,
          trip.id,
          editingBudgetId,
          { category, budget_kyat: budgetKyat },
          requiredEtag(current.etag),
        ))
        setPageNotice('Planned expense updated.')
      } else {
        await authorizedRequest((accessToken) => createTripBudget(
          accessToken,
          trip.id,
          { category, budget_kyat: budgetKyat },
          createIdempotencyKey(),
        ))
        setPageNotice('Planned expense added.')
      }
      setBudgetForm(null)
      setBudgetTripId(null)
      setEditingBudgetId(null)
      setBudgetError('')
      await loadTrips()
    } catch (error) {
      setBudgetError(describeError(
        error,
        'Unable to save this planned expense. Please try again.',
      ))
    } finally {
      endAction()
    }
  }

  async function deleteBudget(trip: Trip, budget: TripCategoryBudget) {
    const linkedSpending = trip.spending.filter((entry) => entry.categoryBudgetId === budget.id).length
    const detail = linkedSpending > 0
      ? ` ${linkedSpending} spending ${linkedSpending === 1 ? 'entry will' : 'entries will'} be kept as unbudgeted trip spending.`
      : ''
    if (!window.confirm(`Remove the ${budget.category} budget?${detail}`)) return
    if (!beginAction('delete-budget')) return

    try {
      const current = await authorizedRequest((accessToken) =>
        fetchTripBudget(accessToken, trip.id, budget.id))
      await authorizedRequest((accessToken) => deleteTripBudget(
        accessToken,
        trip.id,
        budget.id,
        requiredEtag(current.etag),
        createIdempotencyKey(),
      ))
      if (editingBudgetId === budget.id) {
        setBudgetForm(null)
        setBudgetTripId(null)
        setEditingBudgetId(null)
      }
      setPageNotice('Planned expense removed. Linked spending was kept as unbudgeted.')
      await loadTrips()
    } catch (error) {
      setPageError(describeError(
        error,
        'Unable to remove this planned expense. Please try again.',
      ))
    } finally {
      endAction()
    }
  }

  function beginAddSpending(trip: Trip, budget: TripCategoryBudget) {
    closeTripForm()
    setBudgetForm(null)
    setSpendingTripId(trip.id)
    setSpendingBudgetId(budget.id)
    setSpendingForm({ description: '', amount: '', date: localDateString() })
    setSpendingError('')
  }

  async function saveSpending(
    event: FormEvent<HTMLFormElement>,
    trip: Trip,
    budget: TripCategoryBudget,
  ) {
    event.preventDefault()
    if (!spendingForm) return
    const description = spendingForm.description.trim()
    const amountKyat = parseMoneyToKyat(spendingForm.amount, {
      maxAmount: MAX_FINANCIAL_ENTRY_KYAT,
    })
    if (!description) {
      setSpendingError('Enter a description for this spending entry.')
      return
    }
    if (amountKyat === null) {
      setSpendingError('Enter a positive whole-Kyat amount up to 1,000,000,000 MMK.')
      return
    }
    if (!isValidDateOnly(spendingForm.date)) {
      setSpendingError('Enter a valid spending date.')
      return
    }
    if (!beginAction('spending')) return

    try {
      await authorizedRequest((accessToken) => createTripSpending(
        accessToken,
        trip.id,
        {
          description,
          category: budget.category,
          amount_kyat: amountKyat,
          spending_date: spendingForm.date,
          category_budget_id: budget.id,
        },
        createIdempotencyKey(),
      ))
      setSpendingForm(null)
      setSpendingTripId(null)
      setSpendingBudgetId(null)
      setSpendingError('')
      setPageNotice('Spending recorded.')
      await loadTrips()
    } catch (error) {
      setSpendingError(describeError(
        error,
        'Unable to save this spending entry. Please try again.',
      ))
    } finally {
      endAction()
    }
  }

  async function deleteSpending(trip: Trip, entry: TripSpendingEntry) {
    if (!window.confirm(`Delete the spending entry “${entry.description}”?`)) return
    if (!beginAction('delete-spending')) return

    try {
      const current = await authorizedRequest((accessToken) =>
        fetchTripSpendingEntry(accessToken, trip.id, entry.id))
      await authorizedRequest((accessToken) => deleteTripSpending(
        accessToken,
        trip.id,
        entry.id,
        requiredEtag(current.etag),
        createIdempotencyKey(),
      ))
      setPageNotice('Spending entry deleted.')
      await loadTrips()
    } catch (error) {
      setPageError(describeError(
        error,
        'Unable to delete this spending entry. Please try again.',
      ))
    } finally {
      endAction()
    }
  }

  async function handleImportFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setImportError('')
    setImportNotice('')
    if (!beginAction('import')) {
      event.target.value = ''
      return
    }

    try {
      const text = await file.text()
      let document: unknown
      try {
        document = JSON.parse(text)
      } catch {
        throw new Error('The selected file is not valid JSON.')
      }
      const payload = parseTripImportDocument(document)
      const result = await authorizedRequest((accessToken) => importTrips(
        accessToken,
        payload,
        createIdempotencyKey(),
      ))
      setImportNotice(importSummary(result))
      setPageNotice('Travel import completed.')
      await loadTrips()
    } catch (error) {
      setImportError(describeError(
        error,
        'Unable to import this file. Nothing was saved.',
      ))
    } finally {
      event.target.value = ''
      if (fileInputRef.current) fileInputRef.current.value = ''
      endAction()
    }
  }

  return (
    <section className="panel travel-management" aria-labelledby="travel-management-title">
      <div className="management-heading">
        <div>
          <p className="panel-overline">LIVE RECORDS · BACKEND</p>
          <h2 id="travel-management-title">Travel Budget</h2>
          <p className="management-description">Plan trip budgets and track actual spending. Trip spending is separate from Transactions and is not included in transaction reports.</p>
        </div>
        <div className="management-actions">
          <button
            className="text-button"
            type="button"
            onClick={() => {
              setImportOpen((open) => !open)
              setImportError('')
              setImportNotice('')
            }}
          >
            {importOpen ? 'Close import' : 'Import JSON'}
          </button>
          <button className="primary-button" type="button" onClick={beginAddTrip}>
            <span aria-hidden="true">+</span> Add trip
          </button>
        </div>
      </div>

      {pageError && <p className="form-error" role="alert">{pageError}</p>}
      {pageNotice && <p className="form-notice" role="status">{pageNotice}</p>}

      {importOpen && (
        <form className="trip-form" onSubmit={(event) => event.preventDefault()} aria-labelledby="trip-import-title">
          <div className="form-heading">
            <h3 id="trip-import-title">Import travel records</h3>
            <span>JSON · validated before saving</span>
          </div>
          <p className="sample-notice">
            Use a JSON document with a <code>trips</code> array. Each trip accepts
            optional <code>budgets</code> and <code>spending</code> arrays. Exact
            duplicates are skipped, and an invalid file saves nothing.
          </p>
          {importError && <p className="form-error" role="alert">{importError}</p>}
          {importNotice && <p className="form-notice" role="status">{importNotice}</p>}
          <div className="trip-form-fields">
            <label>
              <span>JSON file <b aria-hidden="true">*</b></span>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                disabled={submitting !== null}
                onChange={(event) => { void handleImportFile(event) }}
              />
            </label>
          </div>
          {submitting === 'import' && <p className="form-notice" role="status">Importing and validating…</p>}
        </form>
      )}

      {tripFormOpen && (
        <form className="trip-form" noValidate onSubmit={(event) => { void saveTrip(event) }} aria-labelledby="trip-form-title">
          <div className="form-heading">
            <h3 id="trip-form-title">{editingTripId ? 'Edit trip' : 'Add trip'}</h3>
            <span>Saved to your account</span>
          </div>
          {tripError && <p className="form-error" role="alert">{tripError}</p>}
          <div className="trip-form-fields">
            <label>
              <span>Trip name <b aria-hidden="true">*</b></span>
              <input autoFocus required maxLength={80} value={tripForm.name} onChange={(event) => setTripForm({ ...tripForm, name: event.target.value })} placeholder="e.g. Summer in Rome" />
            </label>
            <label>
              <span>Destination <b aria-hidden="true">*</b></span>
              <input required maxLength={100} value={tripForm.destination} onChange={(event) => setTripForm({ ...tripForm, destination: event.target.value })} placeholder="e.g. Rome, Italy" />
            </label>
            <label>
              <span>Departure <b aria-hidden="true">*</b></span>
              <input required type="date" value={tripForm.startDate} onChange={(event) => setTripForm({ ...tripForm, startDate: event.target.value })} />
            </label>
            <label>
              <span>Return <b aria-hidden="true">*</b></span>
              <input required type="date" value={tripForm.endDate} onChange={(event) => setTripForm({ ...tripForm, endDate: event.target.value })} />
            </label>
          </div>
          <div className="form-actions">
            <button className="text-button" type="button" onClick={closeTripForm}>Cancel</button>
            <button className="primary-button" type="submit" disabled={submitting !== null}>{editingTripId ? 'Save trip' : 'Add trip'}</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="empty-transactions travel-empty" role="status">
          <strong>Loading travel records…</strong>
          <span>Fetching your trips, planned expenses, and spending.</span>
        </div>
      ) : loadError ? (
        <div className="empty-transactions travel-empty" role="alert">
          <strong>Travel records unavailable</strong>
          <span>{loadError}</span>
          <button className="text-button" type="button" onClick={() => { void loadTrips() }}>
            Retry
          </button>
        </div>
      ) : trips.length === 0 ? (
        <div className="empty-transactions travel-empty" role="status">
          <strong>No trips planned yet</strong>
          <span>Add a trip to start building a travel budget.</span>
        </div>
      ) : (
        <div className="trip-record-list">
          {trips.map((trip) => {
            const totalBudgetKyat = getTripBudgetKyat(trip)
            const totalSpentKyat = getTripSpendingKyat(trip)
            const remainingKyat = totalBudgetKyat - totalSpentKyat
            const progress = getBudgetProgressPercent(totalBudgetKyat, totalSpentKyat)
            const tripIsOverBudget = totalSpentKyat > totalBudgetKyat
            const unusedKyat = Math.max(0, totalBudgetKyat - totalSpentKyat)
            const budgetFormOpen = budgetTripId === trip.id && budgetForm !== null

            return (
              <article className="trip-record" key={trip.id} aria-labelledby={`trip-${trip.id}-title`}>
                <div className="trip-record-heading">
                  <div>
                    <p className="panel-overline">{formatTripDate(trip.startDate)} – {formatTripDate(trip.endDate)}</p>
                    <h3 id={`trip-${trip.id}-title`}>{trip.name}</h3>
                    <p className="trip-record-destination">{trip.destination}</p>
                  </div>
                  <div className="trip-record-actions">
                    {trip.status === 'completed' && (
                      <span className="trip-status-badge">Completed</span>
                    )}
                    {trip.status === 'active' && (
                      <button className="icon-button" type="button" disabled={submitting !== null} onClick={() => { void completeTrip(trip) }}>Complete trip</button>
                    )}
                    <button className="icon-button" type="button" disabled={submitting !== null} onClick={() => beginEditTrip(trip)}>Edit trip</button>
                    <button className="icon-button delete-action" type="button" disabled={submitting !== null} onClick={() => { void deleteTrip(trip) }}>Delete trip</button>
                  </div>
                </div>

                <div className="trip-total-metrics">
                  <div><span>Estimated total</span><strong>{formatTripKyat(totalBudgetKyat)}</strong></div>
                  <div><span>Actual spending</span><strong>{formatTripKyat(totalSpentKyat)}</strong></div>
                  <div><span>Remaining</span><strong>{formatTripKyat(remainingKyat)}</strong></div>
                </div>
                <div className="trip-total-progress">
                  <div
                    className="trip-progress-track"
                    role="progressbar"
                    aria-label={`${trip.name} budget usage`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progress}
                  >
                    <span className={tripIsOverBudget ? 'over-budget' : ''} style={{ width: `${progress}%` }} />
                  </div>
                  <span>{progress}% used</span>
                </div>
                {tripIsOverBudget && (
                  <p className="budget-warning" role="status">Trip is {formatTripKyat(totalSpentKyat - totalBudgetKyat)} over its overall budget.</p>
                )}
                {trip.status === 'completed' && (
                  <p className="form-notice trip-completion-notice" role="status">
                    Trip completed. {unusedKyat > 0
                      ? `${formatTripKyat(unusedKyat)} of unused budget was returned to your available balance.`
                      : 'No unused budget was returned.'}{' '}
                    Actual spending of {formatTripKyat(totalSpentKyat)} remains recorded.
                  </p>
                )}

                <div className="trip-budget-section">
                  <div className="trip-subsection-heading">
                    <h4>Planned expenses</h4>
                    <button className="icon-button" type="button" disabled={submitting !== null} onClick={() => beginAddBudget(trip)}>Add category budget</button>
                  </div>
                  {trip.budgets.length > 0 ? (
                    <div className="trip-category-list">
                      {trip.budgets.map((budget) => {
                        const spentKyat = getCategorySpendingKyat(trip, budget.id)
                        const categoryRemaining = budget.budgetKyat - spentKyat
                        const categoryProgress = getBudgetProgressPercent(budget.budgetKyat, spentKyat)
                        const categoryIsOverBudget = spentKyat > budget.budgetKyat
                        const spendingFormOpen = spendingTripId === trip.id && spendingBudgetId === budget.id && spendingForm !== null

                        return (
                          <section className="trip-category" key={budget.id} aria-labelledby={`category-${budget.id}`}>
                            <div className="trip-category-heading">
                              <div>
                                <h5 id={`category-${budget.id}`}>{budget.category}</h5>
                                <span>{formatTripKyat(spentKyat)} spent of {formatTripKyat(budget.budgetKyat)}</span>
                              </div>
                              <div className="trip-category-actions">
                                <button className="icon-button" type="button" disabled={submitting !== null} onClick={() => beginEditBudget(trip, budget)}>Edit budget</button>
                                <button className="icon-button delete-action" type="button" disabled={submitting !== null} onClick={() => { void deleteBudget(trip, budget) }}>Remove</button>
                              </div>
                            </div>
                            <div className="trip-category-metrics">
                              <span>{categoryIsOverBudget ? `${formatTripKyat(spentKyat - budget.budgetKyat)} over budget` : `${formatTripKyat(categoryRemaining)} remaining`}</span>
                              <span>{categoryProgress}% used</span>
                            </div>
                            <div
                              className="category-progress-track"
                              role="progressbar"
                              aria-label={`${budget.category} budget usage`}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={categoryProgress}
                            >
                              <span className={categoryIsOverBudget ? 'over-budget' : ''} style={{ width: `${categoryProgress}%` }} />
                            </div>
                            {categoryIsOverBudget && (
                              <p className="budget-warning" role="status">
                                {budget.budgetKyat === 0
                                  ? 'Spending recorded for a category with no budget set.'
                                  : `Category is ${formatTripKyat(spentKyat - budget.budgetKyat)} over budget.`}
                              </p>
                            )}
                            <button className="text-button spending-button" type="button" disabled={submitting !== null} onClick={() => beginAddSpending(trip, budget)}>Record actual spending</button>

                            {spendingFormOpen && spendingForm && (
                              <form className="trip-spending-form" noValidate onSubmit={(event) => { void saveSpending(event, trip, budget) }} aria-labelledby={`spending-form-${budget.id}`}>
                                <div className="form-heading">
                                  <h6 id={`spending-form-${budget.id}`}>Spending · {budget.category}</h6>
                                  <span>{categoryRemaining > 0 ? `${formatTripKyat(categoryRemaining)} remaining` : 'No budget remaining'}</span>
                                </div>
                                {spendingError && <p className="form-error" role="alert">{spendingError}</p>}
                                <div className="trip-spending-fields">
                                  <label>
                                    <span>Description <b aria-hidden="true">*</b></span>
                                    <input autoFocus required maxLength={100} value={spendingForm.description} onChange={(event) => setSpendingForm({ ...spendingForm, description: event.target.value })} placeholder="e.g. Hotel deposit" />
                                  </label>
                                  <label>
                                    <span>Amount (MMK) <b aria-hidden="true">*</b></span>
                                    <input required type="number" min="1" max={MAX_FINANCIAL_ENTRY_KYAT} step="1" inputMode="numeric" value={spendingForm.amount} onChange={(event) => setSpendingForm({ ...spendingForm, amount: event.target.value })} placeholder="e.g. 250000" />
                                  </label>
                                  <label>
                                    <span>Date <b aria-hidden="true">*</b></span>
                                    <input required type="date" value={spendingForm.date} onChange={(event) => setSpendingForm({ ...spendingForm, date: event.target.value })} />
                                  </label>
                                </div>
                                <div className="form-actions">
                                  <button className="text-button" type="button" onClick={() => { setSpendingForm(null); setSpendingError('') }}>Cancel</button>
                                  <button className="primary-button" type="submit" disabled={submitting !== null}>Save spending</button>
                                </div>
                              </form>
                            )}
                          </section>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="empty-transactions category-empty" role="status">
                      <strong>No planned expenses yet</strong>
                      <span>Add a planned expense to record trip spending.</span>
                    </div>
                  )}
                  {budgetFormOpen && budgetForm && (
                    <form className="trip-budget-form" noValidate onSubmit={(event) => { void saveBudget(event, trip) }} aria-labelledby={`budget-form-${trip.id}`}>
                      <div className="form-heading">
                        <h5 id={`budget-form-${trip.id}`}>{editingBudgetId ? 'Edit planned expense' : 'Add planned expense'}</h5>
                        <span>Zero is allowed; spending will be flagged as over budget.</span>
                      </div>
                      {budgetError && <p className="form-error" role="alert">{budgetError}</p>}
                      <div className="trip-budget-fields">
                        <label>
                          <span>Category <b aria-hidden="true">*</b></span>
                          <input autoFocus required maxLength={50} value={budgetForm.category} onChange={(event) => setBudgetForm({ ...budgetForm, category: event.target.value })} placeholder="e.g. Lodging" />
                        </label>
                        <label>
                          <span>Budget amount (MMK) <b aria-hidden="true">*</b></span>
                          <input required type="number" min="0" step="1" inputMode="numeric" value={budgetForm.amount} onChange={(event) => setBudgetForm({ ...budgetForm, amount: event.target.value })} placeholder="e.g. 500000" />
                        </label>
                      </div>
                      <div className="form-actions">
                        <button className="text-button" type="button" onClick={() => { setBudgetForm(null); setBudgetError('') }}>Cancel</button>
                        <button className="primary-button" type="submit" disabled={submitting !== null}>{editingBudgetId ? 'Save budget' : 'Add budget'}</button>
                      </div>
                    </form>
                  )}
                </div>

                <details className="trip-spending-history">
                  <summary>Actual spending history <span>{trip.spending.length} {trip.spending.length === 1 ? 'entry' : 'entries'}</span></summary>
                  {trip.spending.length > 0 ? (
                    <ul>
                      {trip.spending.map((entry) => (
                        <li key={entry.id}>
                          <span>
                            <strong>{entry.description}</strong>
                            <small>{entry.categoryBudgetId === null ? `${entry.category} · Unbudgeted` : entry.category} · {formatTripDate(entry.date)}</small>
                          </span>
                          <span className="trip-spending-item-actions">
                            <strong>{formatTripKyat(entry.amountKyat)}</strong>
                            <button className="icon-button delete-action" type="button" disabled={submitting !== null} aria-label={`Delete ${entry.description}`} onClick={() => { void deleteSpending(trip, entry) }}>Delete</button>
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="history-empty">No actual spending recorded yet.</p>}
                </details>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

export default TravelBudgetSection
