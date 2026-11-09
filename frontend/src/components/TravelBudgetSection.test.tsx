import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TravelBudgetSection from './TravelBudgetSection'
import { ApiError } from '../api/errors'
import type {
  TripBudgetResource,
  TripResource,
  TripSpendingResource,
} from '../api/travel'

const mocks = vi.hoisted(() => ({
  authorizedRequest: vi.fn(),
  createIdempotencyKey: vi.fn(),
  fetchTrips: vi.fn(),
  fetchTrip: vi.fn(),
  fetchTripBudgets: vi.fn(),
  fetchTripBudget: vi.fn(),
  fetchTripSpending: vi.fn(),
  fetchTripSpendingEntry: vi.fn(),
  createTrip: vi.fn(),
  updateTrip: vi.fn(),
  deleteTrip: vi.fn(),
  completeTrip: vi.fn(),
  createTripBudget: vi.fn(),
  updateTripBudget: vi.fn(),
  deleteTripBudget: vi.fn(),
  createTripSpending: vi.fn(),
  deleteTripSpending: vi.fn(),
  importTrips: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'alex@example.com' },
    authorizedRequest: mocks.authorizedRequest,
  }),
}))

vi.mock('../auth/api', () => ({
  createIdempotencyKey: mocks.createIdempotencyKey,
}))

vi.mock('../api/travel', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/travel')>()
  return {
    ...original,
    fetchTrips: mocks.fetchTrips,
    fetchTrip: mocks.fetchTrip,
    fetchTripBudgets: mocks.fetchTripBudgets,
    fetchTripBudget: mocks.fetchTripBudget,
    fetchTripSpending: mocks.fetchTripSpending,
    fetchTripSpendingEntry: mocks.fetchTripSpendingEntry,
    createTrip: mocks.createTrip,
    updateTrip: mocks.updateTrip,
    deleteTrip: mocks.deleteTrip,
    completeTrip: mocks.completeTrip,
    createTripBudget: mocks.createTripBudget,
    updateTripBudget: mocks.updateTripBudget,
    deleteTripBudget: mocks.deleteTripBudget,
    createTripSpending: mocks.createTripSpending,
    deleteTripSpending: mocks.deleteTripSpending,
    importTrips: mocks.importTrips,
  }
})

const trip: TripResource = {
  id: 'trip-1',
  name: 'Lisbon getaway',
  destination: 'Lisbon, Portugal',
  start_date: '2026-11-14',
  end_date: '2026-11-19',
  status: 'active',
  budget_kyat: 2_400_000,
  spending_kyat: 1_025_000,
}

const budgets: TripBudgetResource[] = [
  { id: 'budget-flights', category: 'Flights', budget_kyat: 1_800_000 },
  { id: 'budget-food', category: 'Food', budget_kyat: 600_000 },
]

const spending: TripSpendingResource[] = [
  {
    id: 'spend-flights',
    description: 'Airfare',
    category: 'Flights',
    category_budget_id: 'budget-flights',
    amount_kyat: 1_000_000,
    spending_date: '2026-10-20',
  },
  {
    id: 'spend-food',
    description: 'Snacks',
    category: 'Food',
    category_budget_id: 'budget-food',
    amount_kyat: 25_000,
    spending_date: '2026-10-21',
  },
]

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function jsonFile(content: string): File {
  const file = new File([content], 'travel.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', {
    value: () => Promise.resolve(content),
  })
  return file
}

function tripArticle(name = 'Lisbon getaway'): HTMLElement {
  return screen.getByRole('article', { name: new RegExp(name) })
}

async function renderLoaded(): Promise<void> {
  render(<TravelBudgetSection />)
  expect(await screen.findByText('Lisbon getaway')).toBeInTheDocument()
}

function openBudgetForm(article = tripArticle()): HTMLFormElement {
  fireEvent.click(within(article).getByRole('button', { name: 'Add category budget' }))
  return document.querySelector('.trip-budget-form') as HTMLFormElement
}

function openSpendingForm(article = tripArticle()): HTMLFormElement {
  const flights = within(article).getByRole('heading', { name: 'Flights' })
  const category = flights.closest('.trip-category') as HTMLElement
  fireEvent.click(within(category).getByRole('button', { name: 'Record actual spending' }))
  return document.querySelector('.trip-spending-form') as HTMLFormElement
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.authorizedRequest.mockImplementation(
    (request: (accessToken: string) => Promise<unknown>) =>
      request('access-token-1'),
  )
  mocks.createIdempotencyKey.mockReturnValue('key-1')
  mocks.fetchTrips.mockResolvedValue([trip])
  mocks.fetchTripBudgets.mockResolvedValue(budgets)
  mocks.fetchTripSpending.mockResolvedValue(spending)
  mocks.fetchTrip.mockResolvedValue({ trip, etag: '"3"' })
  mocks.fetchTripBudget.mockResolvedValue({ budget: budgets[0], etag: '"1"' })
  mocks.fetchTripSpendingEntry.mockResolvedValue({
    spending: spending[0],
    etag: '"1"',
  })
})

describe('TravelBudgetSection backend integration', () => {
  it('loads persisted trips and shows planned, actual, remaining, and category totals', async () => {
    await renderLoaded()

    expect(mocks.fetchTrips).toHaveBeenCalledWith('access-token-1')
    expect(mocks.fetchTripBudgets).toHaveBeenCalledWith('access-token-1', 'trip-1')
    expect(mocks.fetchTripSpending).toHaveBeenCalledWith('access-token-1', 'trip-1')
    expect(screen.getByText('LIVE RECORDS · BACKEND')).toBeInTheDocument()
    expect(screen.queryByText(/SAMPLE RECORDS/)).not.toBeInTheDocument()

    const article = tripArticle()
    expect(within(article).getByText('2,400,000 MMK')).toBeInTheDocument()
    expect(within(article).getByText('1,025,000 MMK')).toBeInTheDocument()
    expect(within(article).getByText('1,375,000 MMK')).toBeInTheDocument()

    const flights = within(article).getByRole('heading', { name: 'Flights' })
      .closest('.trip-category') as HTMLElement
    expect(within(flights).getByText('1,000,000 MMK spent of 1,800,000 MMK'))
      .toBeInTheDocument()
    expect(within(flights).getByText('800,000 MMK remaining')).toBeInTheDocument()
  })

  it('shows a loading state, then a proper empty state', async () => {
    const trips = deferred<TripResource[]>()
    mocks.fetchTrips.mockReturnValueOnce(trips.promise)

    render(<TravelBudgetSection />)
    expect(screen.getByText('Loading travel records…')).toBeInTheDocument()

    trips.resolve([])
    expect(await screen.findByText('No trips planned yet')).toBeInTheDocument()
    expect(screen.getByText(/Add a trip to start building/)).toBeInTheDocument()
  })

  it('shows an authentication error and retries the trip list', async () => {
    mocks.fetchTrips
      .mockRejectedValueOnce(new ApiError({
        status: 401,
        code: 'authentication_required',
        message: 'Authentication is required.',
      }))
      .mockResolvedValueOnce([trip])

    render(<TravelBudgetSection />)
    expect(await screen.findByText(
      /Your session has expired. Sign in again to manage travel records/,
    )).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Lisbon getaway')).toBeInTheDocument()
    expect(mocks.fetchTrips).toHaveBeenCalledTimes(2)
  })

  it('allows planned expenses above the individual-entry limit', async () => {
    mocks.fetchTripBudgets
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { id: 'budget-venue', category: 'Venue', budget_kyat: 2_000_000_001 },
      ])
    mocks.fetchTrips.mockResolvedValue([{ ...trip, budget_kyat: 0, spending_kyat: 0 }])
    mocks.fetchTripSpending.mockResolvedValue([])
    mocks.createTripBudget.mockResolvedValue({
      budget: { id: 'budget-venue', category: 'Venue', budget_kyat: 2_000_000_001 },
      etag: '"1"',
    })
    await renderLoaded()

    const form = openBudgetForm()
    fireEvent.change(within(form).getByLabelText(/Category/), {
      target: { value: 'Venue' },
    })
    fireEvent.change(within(form).getByLabelText(/Budget amount/), {
      target: { value: '2000000001' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Add budget' }))

    await waitFor(() => {
      expect(mocks.createTripBudget).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        { category: 'Venue', budget_kyat: 2_000_000_001 },
        'key-1',
      )
    })
  })

  it('edits a planned expense using the current server revision', async () => {
    mocks.updateTripBudget.mockResolvedValue({
      budget: { ...budgets[0], budget_kyat: 2_000_000 },
      etag: '"2"',
    })
    await renderLoaded()

    const article = tripArticle()
    const flights = within(article).getByRole('heading', { name: 'Flights' })
      .closest('.trip-category') as HTMLElement
    fireEvent.click(within(flights).getByRole('button', { name: 'Edit budget' }))
    const form = document.querySelector('.trip-budget-form') as HTMLFormElement
    fireEvent.change(within(form).getByLabelText(/Budget amount/), {
      target: { value: '2000000' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Save budget' }))

    await waitFor(() => {
      expect(mocks.updateTripBudget).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        'budget-flights',
        { category: 'Flights', budget_kyat: 2_000_000 },
        '"1"',
      )
    })
    expect(await screen.findByText('Planned expense updated.')).toBeInTheDocument()
  })

  it('enforces the individual-entry limit for actual spending', async () => {
    await renderLoaded()
    const form = openSpendingForm()
    fireEvent.change(within(form).getByLabelText(/Description/), {
      target: { value: 'Airfare' },
    })
    const amount = within(form).getByLabelText(/Amount/)

    fireEvent.change(amount, { target: { value: '0' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save spending' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/positive whole-Kyat amount/)

    fireEvent.change(amount, { target: { value: '250.50' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save spending' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/positive whole-Kyat amount/)

    fireEvent.change(amount, { target: { value: '1000000001' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save spending' }))
    expect(screen.getByRole('alert')).toHaveTextContent(/up to 1,000,000,000 MMK/)
    expect(mocks.createTripSpending).not.toHaveBeenCalled()

    fireEvent.change(amount, { target: { value: '1000000000' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save spending' }))

    await waitFor(() => {
      expect(mocks.createTripSpending).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        {
          description: 'Airfare',
          category: 'Flights',
          amount_kyat: 1_000_000_000,
          spending_date: expect.any(String),
          category_budget_id: 'budget-flights',
        },
        'key-1',
      )
    })
  })

  it('warns about over-budget spending without blocking it', async () => {
    mocks.fetchTrips.mockResolvedValue([{
      ...trip,
      budget_kyat: 100_000,
      spending_kyat: 150_000,
    }])
    mocks.fetchTripBudgets.mockResolvedValue([
      { id: 'budget-small', category: 'Flights', budget_kyat: 100_000 },
    ])
    mocks.fetchTripSpending.mockResolvedValue([{
      ...spending[0],
      category_budget_id: 'budget-small',
      amount_kyat: 150_000,
    }])
    mocks.createTripSpending.mockResolvedValue({
      spending: spending[0],
      etag: '"2"',
    })
    await renderLoaded()

    expect(screen.getByText(
      'Trip is 50,000 MMK over its overall budget.',
    )).toBeInTheDocument()

    const form = openSpendingForm()
    fireEvent.change(within(form).getByLabelText(/Description/), {
      target: { value: 'Extra legroom' },
    })
    fireEvent.change(within(form).getByLabelText(/Amount/), {
      target: { value: '250000' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Save spending' }))

    await waitFor(() => {
      expect(mocks.createTripSpending).toHaveBeenCalledOnce()
    })
  })

  it('creates a trip with an idempotency key and blocks duplicate submissions', async () => {
    const created = deferred<{ trip: TripResource; etag: string }>()
    mocks.createTrip.mockReturnValueOnce(created.promise)
    mocks.fetchTrips.mockResolvedValue([])
    render(<TravelBudgetSection />)
    expect(await screen.findByText('No trips planned yet')).toBeInTheDocument()

    const addTrip = screen.getAllByRole('button', { name: 'Add trip' })[0]
    fireEvent.click(addTrip)
    const form = document.querySelector('.trip-form') as HTMLFormElement
    fireEvent.change(within(form).getByLabelText(/Trip name/), {
      target: { value: 'Rome weekend' },
    })
    fireEvent.change(within(form).getByLabelText(/Destination/), {
      target: { value: 'Rome, Italy' },
    })
    fireEvent.change(within(form).getByLabelText(/Departure/), {
      target: { value: '2027-05-01' },
    })
    fireEvent.change(within(form).getByLabelText(/Return/), {
      target: { value: '2027-05-04' },
    })

    const submit = within(form).getByRole('button', { name: 'Add trip' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    await waitFor(() => {
      expect(mocks.createTrip).toHaveBeenCalledOnce()
    })
    expect(mocks.createTrip).toHaveBeenCalledWith(
      'access-token-1',
      {
        name: 'Rome weekend',
        destination: 'Rome, Italy',
        start_date: '2027-05-01',
        end_date: '2027-05-04',
      },
      'key-1',
    )
    created.resolve({ trip: { ...trip, id: 'trip-new', name: 'Rome weekend' }, etag: '"1"' })
  })

  it('imports a validated JSON file and reports created and skipped rows', async () => {
    mocks.importTrips.mockResolvedValue({
      trips_created: 1,
      trips_skipped_duplicates: 0,
      budgets_created: 1,
      budgets_skipped_duplicates: 1,
      spending_created: 1,
      spending_skipped_duplicates: 0,
    })
    await renderLoaded()

    fireEvent.click(screen.getByRole('button', { name: 'Import JSON' }))
    const input = screen.getByLabelText(/JSON file/)
    const content = JSON.stringify({
      trips: [{
        name: 'Kyoto spring trip',
        destination: 'Kyoto, Japan',
        start_date: '2027-03-08',
        end_date: '2027-03-16',
        budgets: [{ category: 'Flights', budget_kyat: 3_200_000 }],
        spending: [{
          description: 'Flight deposit',
          category: 'Flights',
          amount_kyat: 840_000,
          spending_date: '2026-10-21',
          budget_category: 'Flights',
        }],
      }],
    })
    fireEvent.change(input, { target: { files: [jsonFile(content)] } })

    await waitFor(() => {
      expect(mocks.importTrips).toHaveBeenCalledOnce()
    })
    expect(mocks.importTrips).toHaveBeenCalledWith(
      'access-token-1',
      {
        trips: [{
          name: 'Kyoto spring trip',
          destination: 'Kyoto, Japan',
          start_date: '2027-03-08',
          end_date: '2027-03-16',
          budgets: [{ category: 'Flights', budget_kyat: 3_200_000 }],
          spending: [{
            description: 'Flight deposit',
            category: 'Flights',
            amount_kyat: 840_000,
            spending_date: '2026-10-21',
            budget_category: 'Flights',
          }],
        }],
      },
      'key-1',
    )
    expect(await screen.findByText(/Imported 1 trip/)).toBeInTheDocument()
    expect(screen.getByText(/duplicate rows skipped/)).toBeInTheDocument()
  })

  it('rejects invalid import files without calling the API', async () => {
    await renderLoaded()

    fireEvent.click(screen.getByRole('button', { name: 'Import JSON' }))
    const input = screen.getByLabelText(/JSON file/)

    fireEvent.change(input, { target: { files: [jsonFile('not json')] } })
    expect(await screen.findByText(/not valid JSON/)).toBeInTheDocument()
    expect(mocks.importTrips).not.toHaveBeenCalled()

    const badAmount = JSON.stringify({
      trips: [{
        name: 'Bad import',
        destination: 'Nowhere',
        start_date: '2027-01-01',
        end_date: '2027-01-03',
        spending: [{
          description: 'Too large',
          category: 'Other',
          amount_kyat: 1_000_000_001,
          spending_date: '2026-12-01',
        }],
      }],
    })
    fireEvent.change(input, { target: { files: [jsonFile(badAmount)] } })
    expect(await screen.findByText(/cannot exceed 1000000000 MMK/))
      .toBeInTheDocument()
    expect(mocks.importTrips).not.toHaveBeenCalled()
  })

  it('deletes a trip using the current server revision and confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocks.deleteTrip.mockResolvedValue(undefined)
    await renderLoaded()

    fireEvent.click(within(tripArticle()).getByRole('button', { name: 'Delete trip' }))

    await waitFor(() => {
      expect(mocks.deleteTrip).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        '"3"',
        'key-1',
      )
    })
    expect(confirm).toHaveBeenCalledOnce()
    confirm.mockRestore()
  })

  it('removes a planned expense and keeps the refreshed spending history', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocks.deleteTripBudget.mockResolvedValue(undefined)
    mocks.fetchTripBudgets
      .mockResolvedValueOnce(budgets)
      .mockResolvedValueOnce([budgets[1]])
    await renderLoaded()

    const article = tripArticle()
    const flights = within(article).getByRole('heading', { name: 'Flights' })
      .closest('.trip-category') as HTMLElement
    fireEvent.click(within(flights).getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(mocks.deleteTripBudget).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        'budget-flights',
        '"1"',
        'key-1',
      )
    })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Flights' })).not.toBeInTheDocument()
    })
    confirm.mockRestore()
  })

  it('completes a trip after confirmation and reports returned unused budget', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mocks.completeTrip.mockResolvedValue({
      trip: { ...trip, status: 'completed' },
      etag: '"4"',
    })
    mocks.fetchTrips
      .mockResolvedValueOnce([trip])
      .mockResolvedValue([{ ...trip, status: 'completed' }])
    await renderLoaded()

    fireEvent.click(within(tripArticle()).getByRole('button', {
      name: 'Complete trip',
    }))

    await waitFor(() => {
      expect(mocks.completeTrip).toHaveBeenCalledWith(
        'access-token-1',
        'trip-1',
        '"3"',
        'key-1',
      )
    })
    expect(confirm.mock.calls[0][0]).toMatch(
      /1,375,000 MMK of unused budget will be returned/,
    )
    await waitFor(() => {
      expect(
        document.querySelector('.trip-completion-notice')?.textContent,
      ).toContain(
        '1,375,000 MMK of unused budget was returned to your available balance.',
      )
    })
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete trip' }))
      .not.toBeInTheDocument()
    confirm.mockRestore()
  })
})
