import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from './config'
import { ApiError } from './errors'
import {
  fetchFinancialSettings,
  updateFinancialSettings,
  type FinancialSettings,
} from './financialSettings'

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

async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof ApiError) return error
    throw error
  }
  throw new Error('Expected an ApiError to be thrown.')
}

const configuredSettings: FinancialSettings = {
  status: 'configured',
  starting_balance_kyat: 800_000,
  effective_date: '2026-10-31',
  amount_kyat: 825_000,
  as_of_date: '2026-11-03',
}

describe('financial settings API', () => {
  it('loads the caller settings and exposes the ETag', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: configuredSettings },
      { headers: { ETag: '"3"' } },
    ))

    const result = await fetchFinancialSettings('access-token-1')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/users/me/financial-settings`,
    )
    expect(requestInit().method).toBe('GET')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result.settings).toEqual(configuredSettings)
    expect(result.etag).toBe('"3"')
  })

  it('returns a null ETag when the server omits it', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: configuredSettings }))

    const result = await fetchFinancialSettings('access-token-1')

    expect(result.etag).toBeNull()
  })

  it('saves both starting-balance fields with the current If-Match ETag', async () => {
    const updated = {
      ...configuredSettings,
      starting_balance_kyat: -25_000,
      amount_kyat: 0,
    }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: updated },
      { headers: { ETag: '"4"' } },
    ))

    const result = await updateFinancialSettings(
      'access-token-1',
      { starting_balance_kyat: -25_000, effective_date: '2026-11-03' },
      '"3"',
    )

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/users/me/financial-settings`,
    )
    expect(requestInit().method).toBe('PUT')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(requestHeaders().get('If-Match')).toBe('"3"')
    expect(JSON.parse(String(requestInit().body))).toEqual({
      starting_balance_kyat: -25_000,
      effective_date: '2026-11-03',
    })
    expect(result.settings).toEqual(updated)
    expect(result.etag).toBe('"4"')
  })

  it('round-trips a weak ETag from the Pages edge without rewriting it', async () => {
    const initial: FinancialSettings = {
      status: 'not_configured',
      starting_balance_kyat: null,
      effective_date: null,
      amount_kyat: null,
      as_of_date: '2026-11-12',
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(
      { data: initial },
      { headers: { ETag: 'W/"1"' } },
    ))

    const loaded = await fetchFinancialSettings('access-token-1')
    expect(loaded.etag).toBe('W/"1"')

    fetchMock.mockResolvedValueOnce(jsonResponse(
      { data: configuredSettings },
      { headers: { ETag: 'W/"2"' } },
    ))

    const saved = await updateFinancialSettings(
      'access-token-1',
      { starting_balance_kyat: 800_000, effective_date: '2026-11-01' },
      loaded.etag as string,
    )

    expect(requestHeaders(1).get('If-Match')).toBe('W/"1"')
    expect(saved.etag).toBe('W/"2"')
  })

  it('clears both fields together when both are null', async () => {
    const cleared: FinancialSettings = {
      status: 'not_configured',
      starting_balance_kyat: null,
      effective_date: null,
      amount_kyat: null,
      as_of_date: '2026-11-03',
    }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: cleared },
      { headers: { ETag: '"5"' } },
    ))

    const result = await updateFinancialSettings(
      'access-token-1',
      { starting_balance_kyat: null, effective_date: null },
      '"4"',
    )

    expect(JSON.parse(String(requestInit().body))).toEqual({
      starting_balance_kyat: null,
      effective_date: null,
    })
    expect(result.settings.status).toBe('not_configured')
    expect(result.settings.amount_kyat).toBeNull()
  })

  it('rejects an unexpected response shape', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}))

    const error = await captureApiError(fetchFinancialSettings('access-token-1'))

    expect(error.code).toBe('invalid_response')
    expect(error.status).toBe(200)
  })
})
