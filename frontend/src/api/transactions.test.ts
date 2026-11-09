import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import {
  createTransaction,
  deleteTransaction,
  fetchTransaction,
  fetchTransactionCategories,
  fetchTransactions,
  updateTransaction,
  type TransactionCategory,
  type TransactionCreateInput,
  type TransactionResource,
} from './transactions'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return new Response(JSON.stringify(body), { ...init, headers })
}

function requestInit(callIndex = 0): RequestInit {
  return fetchMock.mock.calls[callIndex][1] as RequestInit
}

function requestHeaders(callIndex = 0): Headers {
  return new Headers(requestInit(callIndex).headers)
}

function requestUrl(callIndex = 0): URL {
  return new URL(String(fetchMock.mock.calls[callIndex][0]))
}

async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof ApiError) return error
    throw error
  }
  throw new Error('Expected an ApiError to be thrown.')
}

const category: TransactionCategory = {
  id: 'category-expense-1',
  name: 'Groceries',
  type: 'expense',
}

const incomeCategory: TransactionCategory = {
  id: 'category-income-1',
  name: 'Salary',
  type: 'income',
}

const transaction: TransactionResource = {
  id: 'transaction-1',
  description: 'Market run',
  type: 'expense',
  category_id: category.id,
  category: category.name,
  amount_kyat: 42_000,
  transaction_date: '2026-11-03',
  created_at: '2026-11-03T09:00:00+00:00',
  updated_at: '2026-11-03T09:00:00+00:00',
}

const createInput: TransactionCreateInput = {
  description: 'Market run',
  type: 'expense',
  category_id: category.id,
  amount_kyat: 42_000,
  transaction_date: '2026-11-03',
}

describe('transactions API', () => {
  it('loads active categories with an optional type filter', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      data: [category, incomeCategory],
    }))

    const result = await fetchTransactionCategories('access-token-1', 'expense')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(requestUrl().pathname).toBe('/api/v1/transaction-categories')
    expect(requestUrl().searchParams.get('type')).toBe('expense')
    expect(requestInit().method).toBe('GET')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual([category, incomeCategory])
  })

  it('loads a filtered transaction page with an opaque cursor', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      data: [transaction],
      pagination: { limit: 25, next_cursor: 'cursor-2' },
    }))

    const result = await fetchTransactions('access-token-1', {
      type: 'expense',
      category_id: category.id,
      from: '2026-11-01',
      to: '2026-11-30',
      q: 'market',
      limit: 25,
      cursor: 'cursor-1',
    })

    const url = requestUrl()
    expect(url.pathname).toBe('/api/v1/transactions')
    expect(url.searchParams.get('type')).toBe('expense')
    expect(url.searchParams.get('category_id')).toBe(category.id)
    expect(url.searchParams.get('from')).toBe('2026-11-01')
    expect(url.searchParams.get('to')).toBe('2026-11-30')
    expect(url.searchParams.get('q')).toBe('market')
    expect(url.searchParams.get('limit')).toBe('25')
    expect(url.searchParams.get('cursor')).toBe('cursor-1')
    expect(result.data).toEqual([transaction])
    expect(result.pagination.next_cursor).toBe('cursor-2')
  })

  it('omits empty filters from the list request', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      data: [],
      pagination: { limit: 50, next_cursor: null },
    }))

    await fetchTransactions('access-token-1', {
      type: undefined,
      category_id: undefined,
      from: '',
      to: '',
      q: undefined,
    })

    const url = requestUrl()
    expect(url.searchParams.get('type')).toBeNull()
    expect(url.searchParams.get('category_id')).toBeNull()
    expect(url.searchParams.get('from')).toBeNull()
    expect(url.searchParams.get('to')).toBeNull()
    expect(url.searchParams.get('q')).toBeNull()
  })

  it('reads one transaction and exposes its ETag', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: transaction },
      { headers: { ETag: '"4"' } },
    ))

    const result = await fetchTransaction('access-token-1', transaction.id)

    expect(requestUrl().pathname).toBe(`/api/v1/transactions/${transaction.id}`)
    expect(requestInit().method).toBe('GET')
    expect(result.transaction).toEqual(transaction)
    expect(result.etag).toBe('"4"')
  })

  it('creates a transaction with an idempotency key', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: transaction },
      { status: 201, headers: { ETag: '"1"' } },
    ))

    const result = await createTransaction(
      'access-token-1',
      createInput,
      'idempotency-key-create-1',
    )

    expect(requestUrl().pathname).toBe('/api/v1/transactions')
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-create-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual(createInput)
    expect(result.transaction).toEqual(transaction)
    expect(result.etag).toBe('"1"')
  })

  it('updates a transaction with the current If-Match ETag', async () => {
    const updated = { ...transaction, description: 'Weekly market run' }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: updated },
      { headers: { ETag: '"5"' } },
    ))

    const result = await updateTransaction(
      'access-token-1',
      transaction.id,
      { description: 'Weekly market run' },
      '"4"',
    )

    expect(requestUrl().pathname).toBe(`/api/v1/transactions/${transaction.id}`)
    expect(requestInit().method).toBe('PATCH')
    expect(requestHeaders().get('If-Match')).toBe('"4"')
    expect(JSON.parse(String(requestInit().body))).toEqual({
      description: 'Weekly market run',
    })
    expect(result.transaction).toEqual(updated)
    expect(result.etag).toBe('"5"')
  })

  it('propagates a stale-revision error from an update', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The supplied revision does not match the current transaction.',
        field_errors: [],
      },
    }, { status: 412 }))

    const error = await captureApiError(updateTransaction(
      'access-token-1',
      transaction.id,
      { description: 'Changed' },
      '"3"',
    ))

    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
  })

  it('deletes a transaction with If-Match and an idempotency key', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    await deleteTransaction(
      'access-token-1',
      transaction.id,
      '"6"',
      'idempotency-key-delete-1',
    )

    expect(requestUrl().pathname).toBe(`/api/v1/transactions/${transaction.id}`)
    expect(requestInit().method).toBe('DELETE')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(requestHeaders().get('If-Match')).toBe('"6"')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-delete-1',
    )
  })

  it('rejects an unexpected category or list response shape', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}))
    const categoryError = await captureApiError(
      fetchTransactionCategories('access-token-1'),
    )
    expect(categoryError.code).toBe('invalid_response')
    expect(categoryError.status).toBe(200)

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [transaction] }))
    const listError = await captureApiError(
      fetchTransactions('access-token-1'),
    )
    expect(listError.code).toBe('invalid_response')
    expect(listError.status).toBe(200)
  })
})
