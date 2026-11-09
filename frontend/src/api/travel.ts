import { apiClient, apiRequest, type ApiResponse } from './client'
import { ApiError } from './errors'
import { isValidDateOnly } from '../data/dateOnly'
import { MAX_FINANCIAL_ENTRY_KYAT } from '../data/money'

/** Caller-owned trip with derived planned and actual-spending totals. */
export type TripStatus = 'active' | 'completed' | 'cancelled'

export type TripResource = {
  id: string
  name: string
  destination: string
  start_date: string
  end_date: string
  status: TripStatus
  budget_kyat: number
  spending_kyat: number
}

/** A planned expense. The backend stores its name/category in `category`. */
export type TripBudgetResource = {
  id: string
  category: string
  budget_kyat: number
}

/** Actual trip spending, separate from account transactions. */
export type TripSpendingResource = {
  id: string
  description: string
  category: string
  category_budget_id: string | null
  amount_kyat: number
  spending_date: string
}

export type TripCreateInput = {
  name: string
  destination: string
  start_date: string
  end_date: string
}

export type TripUpdateInput = Partial<TripCreateInput>

export type TripBudgetCreateInput = {
  category: string
  budget_kyat: number
}

export type TripBudgetUpdateInput = {
  category?: string
  budget_kyat?: number
}

export type TripSpendingCreateInput = {
  description: string
  category: string
  amount_kyat: number
  spending_date: string
  category_budget_id?: string | null
}

export type TripSpendingUpdateInput = {
  description?: string
  category?: string
  amount_kyat?: number
  spending_date?: string
  category_budget_id?: string | null
}

export type TripImportBudgetInput = {
  category: string
  budget_kyat: number
}

export type TripImportSpendingInput = {
  description: string
  category: string
  amount_kyat: number
  spending_date: string
  budget_category?: string | null
}

export type TripImportInput = {
  trips: Array<{
    name: string
    destination: string
    start_date: string
    end_date: string
    budgets?: TripImportBudgetInput[]
    spending?: TripImportSpendingInput[]
  }>
}

export type TripImportResult = {
  trips_created: number
  trips_skipped_duplicates: number
  budgets_created: number
  budgets_skipped_duplicates: number
  spending_created: number
  spending_skipped_duplicates: number
}

export type TripView = {
  trip: TripResource
  etag: string | null
}

export type TripBudgetView = {
  budget: TripBudgetResource
  etag: string | null
}

export type TripSpendingView = {
  spending: TripSpendingResource
  etag: string | null
}

type Envelope<T> = {
  data: T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function invalidResponse(
  response: ApiResponse<unknown>,
  message: string,
): ApiError {
  return new ApiError({
    status: response.status,
    code: 'invalid_response',
    message,
    headers: response.headers,
  })
}

function isTripResource(value: unknown): value is TripResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.destination === 'string'
    && typeof value.start_date === 'string'
    && typeof value.end_date === 'string'
    && (value.status === 'active'
      || value.status === 'completed'
      || value.status === 'cancelled')
    && typeof value.budget_kyat === 'number'
    && typeof value.spending_kyat === 'number'
}

function isTripBudgetResource(value: unknown): value is TripBudgetResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.category === 'string'
    && typeof value.budget_kyat === 'number'
}

function isTripSpendingResource(
  value: unknown,
): value is TripSpendingResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.description === 'string'
    && typeof value.category === 'string'
    && (value.category_budget_id === null
      || typeof value.category_budget_id === 'string')
    && typeof value.amount_kyat === 'number'
    && typeof value.spending_date === 'string'
}

function isImportResult(value: unknown): value is TripImportResult {
  const keys: Array<keyof TripImportResult> = [
    'trips_created',
    'trips_skipped_duplicates',
    'budgets_created',
    'budgets_skipped_duplicates',
    'spending_created',
    'spending_skipped_duplicates',
  ]
  return isRecord(value) && keys.every((key) => typeof value[key] === 'number')
}

function tripPath(tripId: string): string {
  return `/trips/${encodeURIComponent(tripId)}`
}

function budgetPath(tripId: string, budgetId: string): string {
  return `${tripPath(tripId)}/budgets/${encodeURIComponent(budgetId)}`
}

function spendingPath(tripId: string, spendingId: string): string {
  return `${tripPath(tripId)}/spending/${encodeURIComponent(spendingId)}`
}

function unwrapTrip(
  response: ApiResponse<Envelope<TripResource>>,
): TripResource {
  const data = response.data?.data
  if (!isTripResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected trip response.',
    )
  }
  return data
}

function unwrapBudget(
  response: ApiResponse<Envelope<TripBudgetResource>>,
): TripBudgetResource {
  const data = response.data?.data
  if (!isTripBudgetResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected planned expense response.',
    )
  }
  return data
}

function unwrapSpending(
  response: ApiResponse<Envelope<TripSpendingResource>>,
): TripSpendingResource {
  const data = response.data?.data
  if (!isTripSpendingResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected spending response.',
    )
  }
  return data
}

export async function fetchTrips(
  accessToken: string,
): Promise<TripResource[]> {
  const response = await apiClient.get<Envelope<TripResource[]>>(
    '/trips',
    { accessToken },
  )
  const trips = response.data?.data
  if (!Array.isArray(trips) || !trips.every(isTripResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected trip list response.',
    )
  }
  return trips
}

export async function fetchTrip(
  accessToken: string,
  tripId: string,
): Promise<TripView> {
  const response = await apiClient.get<Envelope<TripResource>>(
    tripPath(tripId),
    { accessToken },
  )
  return {
    trip: unwrapTrip(response),
    etag: response.headers.get('ETag'),
  }
}

export async function createTrip(
  accessToken: string,
  input: TripCreateInput,
  idempotencyKey: string,
): Promise<TripView> {
  const response = await apiClient.post<Envelope<TripResource>>(
    '/trips',
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    trip: unwrapTrip(response),
    etag: response.headers.get('ETag'),
  }
}

export async function updateTrip(
  accessToken: string,
  tripId: string,
  input: TripUpdateInput,
  etag: string,
): Promise<TripView> {
  const response = await apiClient.patch<Envelope<TripResource>>(
    tripPath(tripId),
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    trip: unwrapTrip(response),
    etag: response.headers.get('ETag'),
  }
}

export async function deleteTrip(
  accessToken: string,
  tripId: string,
  etag: string,
  idempotencyKey: string,
): Promise<void> {
  await apiRequest<void>(tripPath(tripId), {
    method: 'DELETE',
    body: { confirm: true },
    accessToken,
    headers: {
      'If-Match': etag,
      'Idempotency-Key': idempotencyKey,
    },
  })
}

export async function completeTrip(
  accessToken: string,
  tripId: string,
  etag: string,
  idempotencyKey: string,
): Promise<TripView> {
  const response = await apiClient.post<Envelope<TripResource>>(
    `${tripPath(tripId)}/complete`,
    undefined,
    {
      accessToken,
      headers: {
        'If-Match': etag,
        'Idempotency-Key': idempotencyKey,
      },
    },
  )
  return {
    trip: unwrapTrip(response),
    etag: response.headers.get('ETag'),
  }
}

export async function fetchTripBudgets(
  accessToken: string,
  tripId: string,
): Promise<TripBudgetResource[]> {
  const response = await apiClient.get<Envelope<TripBudgetResource[]>>(
    `${tripPath(tripId)}/budgets`,
    { accessToken },
  )
  const budgets = response.data?.data
  if (!Array.isArray(budgets) || !budgets.every(isTripBudgetResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected planned expense list response.',
    )
  }
  return budgets
}

export async function fetchTripBudget(
  accessToken: string,
  tripId: string,
  budgetId: string,
): Promise<TripBudgetView> {
  const response = await apiClient.get<Envelope<TripBudgetResource>>(
    budgetPath(tripId, budgetId),
    { accessToken },
  )
  return {
    budget: unwrapBudget(response),
    etag: response.headers.get('ETag'),
  }
}

export async function createTripBudget(
  accessToken: string,
  tripId: string,
  input: TripBudgetCreateInput,
  idempotencyKey: string,
): Promise<TripBudgetView> {
  const response = await apiClient.post<Envelope<TripBudgetResource>>(
    `${tripPath(tripId)}/budgets`,
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    budget: unwrapBudget(response),
    etag: response.headers.get('ETag'),
  }
}

export async function updateTripBudget(
  accessToken: string,
  tripId: string,
  budgetId: string,
  input: TripBudgetUpdateInput,
  etag: string,
): Promise<TripBudgetView> {
  const response = await apiClient.patch<Envelope<TripBudgetResource>>(
    budgetPath(tripId, budgetId),
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    budget: unwrapBudget(response),
    etag: response.headers.get('ETag'),
  }
}

export async function deleteTripBudget(
  accessToken: string,
  tripId: string,
  budgetId: string,
  etag: string,
  idempotencyKey: string,
): Promise<void> {
  await apiClient.delete(budgetPath(tripId, budgetId), {
    accessToken,
    headers: {
      'If-Match': etag,
      'Idempotency-Key': idempotencyKey,
    },
  })
}

export async function fetchTripSpending(
  accessToken: string,
  tripId: string,
): Promise<TripSpendingResource[]> {
  const response = await apiClient.get<Envelope<TripSpendingResource[]>>(
    `${tripPath(tripId)}/spending`,
    { accessToken },
  )
  const spending = response.data?.data
  if (!Array.isArray(spending) || !spending.every(isTripSpendingResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected spending list response.',
    )
  }
  return spending
}

export async function fetchTripSpendingEntry(
  accessToken: string,
  tripId: string,
  spendingId: string,
): Promise<TripSpendingView> {
  const response = await apiClient.get<Envelope<TripSpendingResource>>(
    spendingPath(tripId, spendingId),
    { accessToken },
  )
  return {
    spending: unwrapSpending(response),
    etag: response.headers.get('ETag'),
  }
}

export async function createTripSpending(
  accessToken: string,
  tripId: string,
  input: TripSpendingCreateInput,
  idempotencyKey: string,
): Promise<TripSpendingView> {
  const response = await apiClient.post<Envelope<TripSpendingResource>>(
    `${tripPath(tripId)}/spending`,
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    spending: unwrapSpending(response),
    etag: response.headers.get('ETag'),
  }
}

export async function updateTripSpending(
  accessToken: string,
  tripId: string,
  spendingId: string,
  input: TripSpendingUpdateInput,
  etag: string,
): Promise<TripSpendingView> {
  const response = await apiClient.patch<Envelope<TripSpendingResource>>(
    spendingPath(tripId, spendingId),
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    spending: unwrapSpending(response),
    etag: response.headers.get('ETag'),
  }
}

export async function deleteTripSpending(
  accessToken: string,
  tripId: string,
  spendingId: string,
  etag: string,
  idempotencyKey: string,
): Promise<void> {
  await apiClient.delete(spendingPath(tripId, spendingId), {
    accessToken,
    headers: {
      'If-Match': etag,
      'Idempotency-Key': idempotencyKey,
    },
  })
}

/**
 * Import validated travel data. The backend validates the entire document
 * before saving, skips exact duplicates, and reports created/skipped counts.
 */
export async function importTrips(
  accessToken: string,
  input: TripImportInput,
  idempotencyKey: string,
): Promise<TripImportResult> {
  const response = await apiClient.post<Envelope<TripImportResult>>(
    '/trips/import',
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  const result = response.data?.data
  if (!isImportResult(result)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected import response.',
    )
  }
  return result
}

function importError(prefix: string, message: string): Error {
  return new Error(`${prefix}: ${message}`)
}

function importString(
  record: Record<string, unknown>,
  key: string,
  prefix: string,
  label: string,
  maximum: number,
): string {
  const value = record[key]
  if (typeof value !== 'string' || !value.trim()) {
    throw importError(prefix, `${label} is required.`)
  }
  const cleaned = value.trim()
  if (cleaned.length > maximum) {
    throw importError(
      prefix,
      `${label} must be ${maximum} characters or fewer.`,
    )
  }
  return cleaned
}

function importDate(
  record: Record<string, unknown>,
  key: string,
  prefix: string,
  label: string,
): string {
  const value = record[key]
  if (typeof value !== 'string' || !isValidDateOnly(value)) {
    throw importError(prefix, `${label} must be a valid YYYY-MM-DD date.`)
  }
  return value
}

function importInteger(
  record: Record<string, unknown>,
  key: string,
  prefix: string,
  label: string,
  options: { allowZero: boolean },
): number {
  const value = record[key]
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw importError(prefix, `${label} must be a whole-Kyat integer.`)
  }
  if (value < 0 || (!options.allowZero && value === 0)) {
    throw importError(
      prefix,
      options.allowZero
        ? `${label} cannot be negative.`
        : `${label} must be greater than zero.`,
    )
  }
  return value
}

function importArray(
  record: Record<string, unknown>,
  key: string,
  prefix: string,
): unknown[] {
  const value = record[key]
  if (value === undefined) return []
  if (!Array.isArray(value)) {
    throw importError(prefix, `"${key}" must be an array.`)
  }
  if (value.length > 500) {
    throw importError(prefix, `"${key}" may contain at most 500 rows.`)
  }
  return value
}

/**
 * Validate a user-selected JSON import document before sending it to the
 * backend. The server repeats validation authoritatively and saves atomically.
 */
export function parseTripImportDocument(value: unknown): TripImportInput {
  if (!isRecord(value) || !Array.isArray(value.trips)) {
    throw new Error('The file must contain a "trips" array.')
  }
  if (value.trips.length === 0) {
    throw new Error('The "trips" array must contain at least one trip.')
  }
  if (value.trips.length > 100) {
    throw new Error('Import at most 100 trips at a time.')
  }

  return {
    trips: value.trips.map((item, tripIndex) => {
      const tripPrefix = `Trip ${tripIndex + 1}`
      if (!isRecord(item)) {
        throw importError(tripPrefix, 'Each trip must be an object.')
      }
      const name = importString(item, 'name', tripPrefix, 'Name', 80)
      const destination = importString(
        item,
        'destination',
        tripPrefix,
        'Destination',
        100,
      )
      const startDate = importDate(
        item,
        'start_date',
        tripPrefix,
        'Start date',
      )
      const endDate = importDate(item, 'end_date', tripPrefix, 'End date')
      if (endDate < startDate) {
        throw importError(
          tripPrefix,
          'The return date cannot be before the departure date.',
        )
      }

      const budgets = importArray(item, 'budgets', tripPrefix).map(
        (budget, budgetIndex) => {
          const prefix = `${tripPrefix}, planned expense ${budgetIndex + 1}`
          if (!isRecord(budget)) {
            throw importError(
              prefix,
              'Each planned expense must be an object.',
            )
          }
          return {
            category: importString(
              budget,
              'category',
              prefix,
              'Expense name',
              50,
            ),
            budget_kyat: importInteger(
              budget,
              'budget_kyat',
              prefix,
              'Planned amount',
              { allowZero: true },
            ),
          }
        },
      )

      const spending = importArray(item, 'spending', tripPrefix).map(
        (entry, spendingIndex) => {
          const prefix = `${tripPrefix}, spending ${spendingIndex + 1}`
          if (!isRecord(entry)) {
            throw importError(
              prefix,
              'Each spending entry must be an object.',
            )
          }
          const amountKyat = importInteger(
            entry,
            'amount_kyat',
            prefix,
            'Spending amount',
            { allowZero: false },
          )
          if (amountKyat > MAX_FINANCIAL_ENTRY_KYAT) {
            throw importError(
              prefix,
              `Spending amount cannot exceed ${MAX_FINANCIAL_ENTRY_KYAT} MMK.`,
            )
          }
          const budgetCategory = entry.budget_category
          if (
            budgetCategory !== undefined
            && budgetCategory !== null
            && (typeof budgetCategory !== 'string'
              || !budgetCategory.trim())
          ) {
            throw importError(
              prefix,
              'Budget category must be a non-empty string when provided.',
            )
          }
          return {
            description: importString(
              entry,
              'description',
              prefix,
              'Description',
              100,
            ),
            category: importString(
              entry,
              'category',
              prefix,
              'Category',
              100,
            ),
            amount_kyat: amountKyat,
            spending_date: importDate(
              entry,
              'spending_date',
              prefix,
              'Spending date',
            ),
            budget_category:
              typeof budgetCategory === 'string'
                ? budgetCategory.trim()
                : null,
          }
        },
      )

      return {
        name,
        destination,
        start_date: startDate,
        end_date: endDate,
        budgets,
        spending,
      }
    }),
  }
}
