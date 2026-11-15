import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  API_BASE_URL,
  buildApiUrl,
  DEFAULT_API_BASE_URL,
  defaultApiBaseUrl,
  resolveApiBaseUrl,
} from './config'
import { apiClient } from './client'
import { ApiError } from './errors'

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

function getRequestInit(callIndex = 0): RequestInit {
  return fetchMock.mock.calls[callIndex][1] as RequestInit
}

function getRequestHeaders(callIndex = 0): Headers {
  return new Headers(getRequestInit(callIndex).headers)
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

describe('API configuration', () => {
  it('uses the local API default and normalizes configured base URLs', () => {
    expect(DEFAULT_API_BASE_URL).toBe('http://localhost:8000/api/v1')
    expect(resolveApiBaseUrl(undefined)).toBe(DEFAULT_API_BASE_URL)
    expect(resolveApiBaseUrl('   ')).toBe(DEFAULT_API_BASE_URL)
    expect(resolveApiBaseUrl('  http://api.example.test/api/v1/  ')).toBe(
      'http://api.example.test/api/v1',
    )
    expect(API_BASE_URL).toBe(resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL))
  })

  it('uses a same-origin API path for production builds', () => {
    expect(defaultApiBaseUrl(true)).toBe('/api/v1')
    expect(resolveApiBaseUrl(undefined, defaultApiBaseUrl(true))).toBe('/api/v1')
    expect(resolveApiBaseUrl('   ', defaultApiBaseUrl(true))).toBe('/api/v1')
    expect(defaultApiBaseUrl(false)).toBe('http://localhost:8000/api/v1')
    expect(resolveApiBaseUrl(undefined, defaultApiBaseUrl(false))).toBe(
      'http://localhost:8000/api/v1',
    )
    expect(resolveApiBaseUrl('/api/v1/', defaultApiBaseUrl(true))).toBe(
      '/api/v1',
    )
  })

  it('builds request URLs from one base URL without duplicate slashes', () => {
    expect(buildApiUrl('/transactions', 'http://api.example.test/api/v1/')).toBe(
      'http://api.example.test/api/v1/transactions',
    )
    expect(buildApiUrl('transactions', 'http://api.example.test/api/v1')).toBe(
      'http://api.example.test/api/v1/transactions',
    )
    expect(buildApiUrl('', 'http://api.example.test/api/v1/')).toBe(
      'http://api.example.test/api/v1',
    )
  })
})

describe('API client requests', () => {
  it('sends GET requests and returns successful JSON responses', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: { status: 'ok' } }))

    const response = await apiClient.get<{ data: { status: string } }>('/health')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/health`)
    expect(getRequestInit().method).toBe('GET')
    expect(getRequestHeaders().get('Accept')).toBe('application/json')
    expect(response.data).toEqual({ data: { status: 'ok' } })
    expect(response.status).toBe(200)
  })

  it('serializes POST bodies as JSON and sets the content type', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: { id: 'transaction-1' } }))

    await apiClient.post('/transactions', {
      description: 'Market',
      amount_kyat: 12500,
    })

    const request = getRequestInit()
    expect(request.method).toBe('POST')
    expect(request.body).toBe(
      '{"description":"Market","amount_kyat":12500}',
    )
    expect(getRequestHeaders().get('Content-Type')).toBe('application/json')
  })

  it('supports PUT, PATCH, and DELETE methods', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ data: { updated: true } }))
      .mockResolvedValueOnce(jsonResponse({ data: { updated: true } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await apiClient.put('/settings', { value: 1 })
    await apiClient.patch('/settings', { value: 2 })
    await apiClient.delete('/settings')

    expect(getRequestInit(0).method).toBe('PUT')
    expect(getRequestInit(1).method).toBe('PATCH')
    expect(getRequestInit(2).method).toBe('DELETE')
  })

  it('handles empty responses without a body', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    const response = await apiClient.delete('/transactions/transaction-1')

    expect(response.status).toBe(204)
    expect(response.data).toBeUndefined()
  })

  it('returns successful non-JSON responses as text', async () => {
    fetchMock.mockResolvedValue(new Response('Section,Amount\r\nSummary,100', {
      status: 200,
      headers: { 'Content-Type': 'text/csv; charset=utf-8' },
    }))

    const response = await apiClient.get<string>('/reports/export.csv')

    expect(response.data).toContain('Section,Amount')
  })
})

describe('API errors', () => {
  it('parses the backend error envelope and preserves response headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The resource has changed. Reload and try again.',
        field_errors: [],
        request_id: 'request-123',
      },
    }, {
      status: 412,
      headers: { ETag: '"3"' },
    }))

    const error = await captureApiError(apiClient.patch('/settings', { value: 1 }))

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
    expect(error.message).toBe('The resource has changed. Reload and try again.')
    expect(error.requestId).toBe('request-123')
    expect(error.fieldErrors).toEqual([])
    expect(error.headers.get('ETag')).toBe('"3"')
    expect(error.isNetworkError).toBe(false)
  })

  it('preserves validation error details', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'validation_error',
        message: 'The request contains invalid values.',
        field_errors: [
          {
            field: 'amount_kyat',
            code: 'int_parsing',
            message: 'Input should be a valid integer.',
          },
          {
            field: 'effective_date',
            code: 'value_error',
            message: 'Use a valid calendar date.',
          },
        ],
        request_id: 'request-456',
      },
    }, { status: 422 }))

    const error = await captureApiError(apiClient.post('/transactions', {}))

    expect(error.status).toBe(422)
    expect(error.code).toBe('validation_error')
    expect(error.fieldErrors).toEqual([
      {
        field: 'amount_kyat',
        code: 'int_parsing',
        message: 'Input should be a valid integer.',
      },
      {
        field: 'effective_date',
        code: 'value_error',
        message: 'Use a valid calendar date.',
      },
    ])
  })

  it('preserves an HTTP status when the error response is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('Service unavailable', {
      status: 503,
      statusText: 'Service Unavailable',
      headers: { 'Content-Type': 'text/plain' },
    }))

    const error = await captureApiError(apiClient.get('/dashboard/overview'))

    expect(error.status).toBe(503)
    expect(error.code).toBe('http_error')
    expect(error.message).toBe('Service Unavailable')
    expect(error.isNetworkError).toBe(false)
  })

  it('distinguishes network failures from HTTP errors', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await captureApiError(apiClient.get('/transactions'))

    expect(error.status).toBeNull()
    expect(error.code).toBe('network_error')
    expect(error.isNetworkError).toBe(true)
    expect(error.headers).toEqual(new Headers())
  })
})

describe('authentication-ready request options', () => {
  it('injects a Bearer access token', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    await apiClient.get('/transactions', { accessToken: 'access-token-value' })

    expect(getRequestHeaders().get('Authorization')).toBe(
      'Bearer access-token-value',
    )
  })

  it('sends cookie credentials only when requested', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ data: {} }))
      .mockResolvedValueOnce(jsonResponse({ data: {} }))

    await apiClient.post('/auth/refresh', undefined, {
      credentials: 'include',
    })
    await apiClient.get('/transactions')

    expect(getRequestInit(0).credentials).toBe('include')
    expect(getRequestInit(1).credentials).toBeUndefined()
  })

  it('adds the CSRF header when a token is supplied', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: {} }))

    await apiClient.post('/auth/logout', undefined, {
      credentials: 'include',
      csrfToken: 'csrf-token-value',
    })

    expect(getRequestHeaders().get('X-CSRF-Token')).toBe('csrf-token-value')
  })
})

describe('financial API headers', () => {
  it('passes Idempotency-Key and If-Match through caller headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: { id: 'transaction-1' } }))

    await apiClient.patch('/transactions/transaction-1', { amount_kyat: 100 }, {
      headers: {
        'Idempotency-Key': 'idempotency-key-value',
        'If-Match': '"7"',
      },
    })

    const headers = getRequestHeaders()
    expect(headers.get('Idempotency-Key')).toBe('idempotency-key-value')
    expect(headers.get('If-Match')).toBe('"7"')
  })

  it('exposes response headers such as ETag to callers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: { id: 'transaction-1' } }, {
      headers: { ETag: '"9"' },
    }))

    const response = await apiClient.get('/transactions/transaction-1')

    expect(response.headers.get('ETag')).toBe('"9"')
  })
})

describe('request cancellation', () => {
  it('passes AbortSignal to fetch and propagates an abort without wrapping it', async () => {
    const controller = new AbortController()
    const abortError = new DOMException('The operation was aborted.', 'AbortError')
    fetchMock.mockRejectedValue(abortError)

    await expect(apiClient.get('/transactions', {
      signal: controller.signal,
    })).rejects.toBe(abortError)

    expect(getRequestInit().signal).toBe(controller.signal)
  })
})

describe('request ID correlation', () => {
  // Version 4 UUID as produced by the platform cryptographic generator.
  const uuidPattern =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

  it('sends a cryptographic X-Request-ID with each request', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: {} }))

    await apiClient.get('/transactions')

    const sent = getRequestHeaders().get('X-Request-ID')
    expect(sent).toBeTruthy()
    expect(sent).toMatch(uuidPattern)
  })

  it('sends a different X-Request-ID for separate requests', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ data: {} }))
      .mockResolvedValueOnce(jsonResponse({ data: {} }))

    await apiClient.get('/transactions')
    await apiClient.get('/transactions')

    const first = getRequestHeaders(0).get('X-Request-ID')
    const second = getRequestHeaders(1).get('X-Request-ID')

    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(first).not.toBe(second)
  })

  it('preserves caller-supplied headers and an explicit X-Request-ID', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: {} }))

    await apiClient.get('/transactions', {
      headers: {
        'X-Request-ID': 'caller-supplied-id',
        'X-Custom-Header': 'custom-value',
      },
    })

    const headers = getRequestHeaders()
    expect(headers.get('X-Request-ID')).toBe('caller-supplied-id')
    expect(headers.get('X-Custom-Header')).toBe('custom-value')
    expect(headers.get('Accept')).toBe('application/json')
  })

  it('exposes the response X-Request-ID without changing the response body', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: { status: 'ok' } },
      { headers: { 'X-Request-ID': 'response-request-id' } },
    ))

    const response = await apiClient.get<{ data: { status: string } }>('/health')

    expect(response.requestId).toBe('response-request-id')
    expect(response.data).toEqual({ data: { status: 'ok' } })
    expect(response.status).toBe(200)
    expect(response.headers.get('X-Request-ID')).toBe('response-request-id')
  })

  it('prefers the response header request ID on failure', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The resource has changed. Reload and try again.',
        field_errors: [],
        request_id: 'envelope-request-id',
      },
    }, {
      status: 412,
      headers: { 'X-Request-ID': 'header-request-id' },
    }))

    const error = await captureApiError(apiClient.patch('/settings', { value: 1 }))

    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
    expect(error.message).toBe('The resource has changed. Reload and try again.')
    expect(error.requestId).toBe('header-request-id')
  })

  it('falls back to the error-envelope request_id when the header is absent', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'validation_error',
        message: 'The request contains invalid values.',
        field_errors: [],
        request_id: 'envelope-request-id',
      },
    }, { status: 422 }))

    const error = await captureApiError(apiClient.post('/transactions', {}))

    expect(error.code).toBe('validation_error')
    expect(error.requestId).toBe('envelope-request-id')
  })

  it('exposes a response request ID on non-JSON errors without changing status', async () => {
    fetchMock.mockResolvedValue(new Response('Service unavailable', {
      status: 503,
      statusText: 'Service Unavailable',
      headers: {
        'Content-Type': 'text/plain',
        'X-Request-ID': 'header-request-id',
      },
    }))

    const error = await captureApiError(apiClient.get('/dashboard/overview'))

    expect(error.status).toBe(503)
    expect(error.code).toBe('http_error')
    expect(error.message).toBe('Service Unavailable')
    expect(error.requestId).toBe('header-request-id')
  })

  it('ignores missing, empty, or malformed response request IDs', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ data: {} }))
      .mockResolvedValueOnce(jsonResponse(
        { data: {} },
        { headers: { 'X-Request-ID': 'bad id with spaces' } },
      ))
      .mockResolvedValueOnce(jsonResponse(
        { data: {} },
        { headers: { 'X-Request-ID': '' } },
      ))

    const missing = await apiClient.get('/transactions')
    const malformed = await apiClient.get('/transactions')
    const empty = await apiClient.get('/transactions')

    expect(missing.requestId).toBeUndefined()
    expect(malformed.requestId).toBeUndefined()
    expect(empty.requestId).toBeUndefined()
  })

  it('does not send a request ID header when cryptographic UUIDs are unavailable', async () => {
    const originalCrypto = globalThis.crypto
    // Simulate an environment without a cryptographic UUID generator.
    vi.stubGlobal('crypto', {
      getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto),
    })
    fetchMock.mockResolvedValue(jsonResponse({ data: {} }))

    try {
      await apiClient.get('/transactions')

      expect(getRequestHeaders().has('X-Request-ID')).toBe(false)
    } finally {
      vi.stubGlobal('crypto', originalCrypto)
    }
  })

  it('exposes the generated request ID when the network fails', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await captureApiError(apiClient.get('/transactions'))

    expect(error.status).toBeNull()
    expect(error.code).toBe('network_error')
    expect(error.isNetworkError).toBe(true)

    const sent = getRequestHeaders().get('X-Request-ID')
    expect(sent).toBeTruthy()
    expect(sent).toMatch(uuidPattern)
    expect(error.requestId).toBe(sent)
  })

  it('preserves a valid caller-supplied request ID on network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await captureApiError(apiClient.get('/transactions', {
      headers: { 'X-Request-ID': 'caller-supplied-id' },
    }))

    expect(getRequestHeaders().get('X-Request-ID')).toBe('caller-supplied-id')
    expect(error.code).toBe('network_error')
    expect(error.requestId).toBe('caller-supplied-id')
  })

  it('does not expose a malformed caller-supplied request ID as a correlation ID', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await captureApiError(apiClient.get('/transactions', {
      headers: { 'X-Request-ID': 'not a valid request id' },
    }))

    const sent = getRequestHeaders().get('X-Request-ID')
    expect(sent).not.toBe('not a valid request id')
    expect(sent).toBeTruthy()
    expect(sent).toMatch(uuidPattern)
    expect(error.requestId).toBe(sent)
  })

  it('leaves abort behavior unchanged when the network request is cancelled', async () => {
    const controller = new AbortController()
    const abortError = new DOMException('The operation was aborted.', 'AbortError')
    fetchMock.mockRejectedValue(abortError)

    await expect(apiClient.get('/transactions', {
      signal: controller.signal,
    })).rejects.toBe(abortError)
  })

  it('keeps response-header precedence over the envelope on HTTP errors', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The resource has changed.',
        field_errors: [],
        request_id: 'envelope-request-id',
      },
    }, {
      status: 412,
      headers: { 'X-Request-ID': 'header-request-id' },
    }))

    const error = await captureApiError(apiClient.patch('/settings', { value: 1 }))

    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
    expect(error.requestId).toBe('header-request-id')
  })
})
