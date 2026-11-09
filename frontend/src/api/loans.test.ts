import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import {
  correctRepayment,
  createLoan,
  createRepayment,
  fetchLoan,
  fetchLoans,
  fetchRepayment,
  fetchRepayments,
  updateLoan,
  type LoanCreateInput,
  type LoanResource,
  type RepaymentResource,
} from './loans'

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

const loan: LoanResource = {
  id: 'loan-1',
  name: 'Home loan',
  principal_kyat: 180_000_000,
  start_date: '2022-05-12',
  total_repaid_kyat: 17_750_000,
  outstanding_principal_kyat: 162_250_000,
  created_at: '2022-05-12T09:00:00+00:00',
  updated_at: '2026-11-03T09:00:00+00:00',
}

const repayment: RepaymentResource = {
  id: 'repayment-1',
  amount_kyat: 12_000_000,
  repayment_date: '2026-10-01',
  note: 'Monthly payment',
  created_at: '2026-10-01T09:00:00+00:00',
  corrected: false,
}

const createInput: LoanCreateInput = {
  name: 'Home loan',
  principal_kyat: 180_000_000,
  start_date: '2022-05-12',
}

describe('loans API', () => {
  it('lists owner-scoped loans with backend-derived totals', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [loan] }))

    const result = await fetchLoans('access-token-1')

    expect(requestUrl().pathname).toBe('/api/v1/loans')
    expect(requestInit().method).toBe('GET')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual([loan])
  })

  it('reads one loan and exposes its ETag', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: loan },
      { headers: { ETag: '"4"' } },
    ))

    const result = await fetchLoan('access-token-1', loan.id)

    expect(requestUrl().pathname).toBe(`/api/v1/loans/${loan.id}`)
    expect(result.loan).toEqual(loan)
    expect(result.etag).toBe('"4"')
  })

  it('creates a loan with an idempotency key', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: loan },
      { status: 201, headers: { ETag: '"1"' } },
    ))

    const result = await createLoan(
      'access-token-1',
      createInput,
      'idempotency-key-loan-1',
    )

    expect(requestUrl().pathname).toBe('/api/v1/loans')
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-loan-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual(createInput)
    expect(result.loan).toEqual(loan)
    expect(result.etag).toBe('"1"')
  })

  it('updates a loan with the current If-Match ETag', async () => {
    const updated = { ...loan, name: 'Updated home loan' }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: updated },
      { headers: { ETag: '"5"' } },
    ))

    const result = await updateLoan(
      'access-token-1',
      loan.id,
      { name: 'Updated home loan' },
      '"4"',
    )

    expect(requestUrl().pathname).toBe(`/api/v1/loans/${loan.id}`)
    expect(requestInit().method).toBe('PATCH')
    expect(requestHeaders().get('If-Match')).toBe('"4"')
    expect(JSON.parse(String(requestInit().body))).toEqual({
      name: 'Updated home loan',
    })
    expect(result.etag).toBe('"5"')
  })

  it('propagates a stale-revision loan update error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The supplied revision does not match the current loan.',
        field_errors: [],
      },
    }, { status: 412 }))

    const error = await captureApiError(updateLoan(
      'access-token-1',
      loan.id,
      { name: 'Changed' },
      '"3"',
    ))

    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
  })

  it('loads a loan repayment history', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [repayment] }))

    const result = await fetchRepayments('access-token-1', loan.id)

    expect(requestUrl().pathname).toBe(
      `/api/v1/loans/${loan.id}/repayments`,
    )
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual([repayment])
  })

  it('records a repayment with an idempotency key', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: repayment },
      { status: 201, headers: { ETag: '"2"' } },
    ))

    const result = await createRepayment(
      'access-token-1',
      loan.id,
      {
        amount_kyat: 12_000_000,
        repayment_date: '2026-10-01',
        note: 'Monthly payment',
      },
      'idempotency-key-repayment-1',
    )

    expect(requestUrl().pathname).toBe(
      `/api/v1/loans/${loan.id}/repayments`,
    )
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-repayment-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual({
      amount_kyat: 12_000_000,
      repayment_date: '2026-10-01',
      note: 'Monthly payment',
    })
    expect(result.repayment).toEqual(repayment)
    expect(result.etag).toBe('"2"')
  })

  it('reads one repayment and appends a full-reversal correction', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(
        { data: repayment },
        { headers: { ETag: '"1"' } },
      ))
      .mockResolvedValueOnce(jsonResponse({
        data: {
          original_repayment: { ...repayment, corrected: true },
          correction: {
            id: 'correction-1',
            original_repayment_id: repayment.id,
            amount_kyat: repayment.amount_kyat,
            type: 'reversal',
            reason: 'Recorded twice',
            created_by_user_id: 'user-1',
            created_at: '2026-11-03T09:00:00+00:00',
          },
          outstanding_principal_kyat: loan.principal_kyat,
        },
      }, { status: 201, headers: { ETag: '"2"' } }))

    const view = await fetchRepayment(
      'access-token-1',
      loan.id,
      repayment.id,
    )
    expect(requestUrl().pathname).toBe(
      `/api/v1/loans/${loan.id}/repayments/${repayment.id}`,
    )
    expect(view.etag).toBe('"1"')

    const result = await correctRepayment(
      'access-token-1',
      loan.id,
      repayment.id,
      'Recorded twice',
      'idempotency-key-correction-1',
    )

    expect(requestUrl(1).pathname).toBe(
      `/api/v1/loans/${loan.id}/repayments/${repayment.id}/corrections`,
    )
    expect(requestInit(1).method).toBe('POST')
    expect(requestHeaders(1).get('Idempotency-Key')).toBe(
      'idempotency-key-correction-1',
    )
    expect(JSON.parse(String(requestInit(1).body))).toEqual({
      reason: 'Recorded twice',
    })
    expect(result.result.original_repayment.corrected).toBe(true)
    expect(result.result.outstanding_principal_kyat).toBe(loan.principal_kyat)
    expect(result.etag).toBe('"2"')
  })

  it('rejects unexpected loan, repayment, and correction response shapes', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: 'bad' }] }))
    expect((await captureApiError(fetchLoans('access-token-1'))).code)
      .toBe('invalid_response')

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: 'bad' }] }))
    expect((await captureApiError(
      fetchRepayments('access-token-1', loan.id),
    )).code).toBe('invalid_response')

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: { reason: '' } }))
    expect((await captureApiError(
      correctRepayment('access-token-1', loan.id, repayment.id, '', 'key'),
    )).code).toBe('invalid_response')
  })
})
