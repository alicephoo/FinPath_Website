import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'

export type TransactionType = 'income' | 'expense'

export type TransactionCategory = {
  id: string
  name: string
  type: TransactionType
}

/**
 * Transaction resource returned by list, read, create, and update. The
 * backend-derived `category` display name is included, while `category_id`
 * remains the stable identifier used for writes and filters.
 */
export type TransactionResource = {
  id: string
  description: string
  type: TransactionType
  category_id: string
  category: string
  amount_kyat: number
  transaction_date: string
  created_at: string
  updated_at: string
}

export type TransactionListFilters = {
  type?: TransactionType
  category_id?: string
  /** Inclusive local-calendar date in YYYY-MM-DD form. */
  from?: string
  /** Inclusive local-calendar date in YYYY-MM-DD form. */
  to?: string
  /** Case-insensitive description substring match. */
  q?: string
  limit?: number
  cursor?: string
}

export type TransactionPage = {
  data: TransactionResource[]
  pagination: {
    limit: number
    next_cursor: string | null
  }
}

export type TransactionCreateInput = {
  description: string
  type: TransactionType
  category_id: string
  amount_kyat: number
  transaction_date: string
}

/** Partial-update body accepted by PATCH; fields may be omitted but not null. */
export type TransactionUpdateInput = Partial<TransactionCreateInput>

export type TransactionView = {
  transaction: TransactionResource
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

function isTransactionResource(value: unknown): value is TransactionResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.description === 'string'
    && (value.type === 'income' || value.type === 'expense')
    && typeof value.category_id === 'string'
    && typeof value.category === 'string'
    && typeof value.amount_kyat === 'number'
    && typeof value.transaction_date === 'string'
    && typeof value.created_at === 'string'
    && typeof value.updated_at === 'string'
}

function unwrapTransaction(
  response: ApiResponse<Envelope<TransactionResource>>,
): TransactionResource {
  const data = response.data?.data
  if (!isTransactionResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected transaction response.',
    )
  }
  return data
}

function buildQuery(
  parameters: Record<string, string | number | undefined | null>,
): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(parameters)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

/**
 * List the active system-defined categories. Category creation and mutation
 * are not exposed to ordinary users.
 */
export async function fetchTransactionCategories(
  accessToken: string,
  type?: TransactionType,
): Promise<TransactionCategory[]> {
  const response = await apiClient.get<Envelope<TransactionCategory[]>>(
    `/transaction-categories${buildQuery({ type })}`,
    { accessToken },
  )
  const categories = response.data?.data
  if (!Array.isArray(categories)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected transaction category response.',
    )
  }
  return categories
}

/**
 * List owner-scoped transactions using backend filters and an opaque cursor.
 * Collection responses do not include per-item ETags.
 */
export async function fetchTransactions(
  accessToken: string,
  filters: TransactionListFilters = {},
): Promise<TransactionPage> {
  const response = await apiClient.get<TransactionPage>(
    `/transactions${buildQuery({
      type: filters.type,
      category_id: filters.category_id,
      from: filters.from,
      to: filters.to,
      q: filters.q,
      limit: filters.limit,
      cursor: filters.cursor,
    })}`,
    { accessToken },
  )
  const page = response.data
  if (
    !page
    || !Array.isArray(page.data)
    || !page.data.every(isTransactionResource)
    || !isRecord(page.pagination)
    || typeof page.pagination.limit !== 'number'
    || !(
      page.pagination.next_cursor === null
      || typeof page.pagination.next_cursor === 'string'
    )
  ) {
    throw invalidResponse(
      response,
      'The server returned an unexpected transaction list response.',
    )
  }
  return page
}

/**
 * Read one transaction and expose its current ETag. Collection responses omit
 * ETags, so the UI must call this before PATCH or DELETE.
 */
export async function fetchTransaction(
  accessToken: string,
  transactionId: string,
): Promise<TransactionView> {
  const response = await apiClient.get<Envelope<TransactionResource>>(
    `/transactions/${encodeURIComponent(transactionId)}`,
    { accessToken },
  )
  return {
    transaction: unwrapTransaction(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Create a transaction. The caller supplies the idempotency key so it can
 * reuse the same key when retrying an uncertain failure of this operation.
 */
export async function createTransaction(
  accessToken: string,
  input: TransactionCreateInput,
  idempotencyKey: string,
): Promise<TransactionView> {
  const response = await apiClient.post<Envelope<TransactionResource>>(
    '/transactions',
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    transaction: unwrapTransaction(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Partially update a transaction. The current ETag must be supplied so stale
 * writes are rejected with `412 stale_revision` instead of overwriting data.
 */
export async function updateTransaction(
  accessToken: string,
  transactionId: string,
  input: TransactionUpdateInput,
  etag: string,
): Promise<TransactionView> {
  const response = await apiClient.patch<Envelope<TransactionResource>>(
    `/transactions/${encodeURIComponent(transactionId)}`,
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    transaction: unwrapTransaction(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Delete a transaction with the current ETag and an idempotency key. The
 * caller owns key reuse for retries of the same delete operation.
 */
export async function deleteTransaction(
  accessToken: string,
  transactionId: string,
  etag: string,
  idempotencyKey: string,
): Promise<void> {
  await apiClient.delete(
    `/transactions/${encodeURIComponent(transactionId)}`,
    {
      accessToken,
      headers: {
        'If-Match': etag,
        'Idempotency-Key': idempotencyKey,
      },
    },
  )
}
