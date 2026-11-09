import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { onRequest } from '../functions/api/[[path]]'
import {
  buildUpstreamUrl,
  copyUpstreamHeaders,
  proxyRequest,
  type PagesFunctionEnv,
} from '../functions/api/proxy'

const BACKEND = 'https://finpath-backend.onrender.com'
const PAGES_ORIGIN = 'https://finpath.pages.dev'
const fetchMock = vi.fn<typeof fetch>()

function env(
  backendUrl: string | undefined = BACKEND,
): PagesFunctionEnv {
  return { RENDER_BACKEND_URL: backendUrl }
}

function context(
  path: string,
  init: RequestInit = {},
  environment: PagesFunctionEnv = env(),
) {
  return {
    request: new Request(`${PAGES_ORIGIN}${path}`, init),
    env: environment,
  }
}

function jsonResponse(
  body: unknown,
  init: ResponseInit = {},
): Response {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return new Response(JSON.stringify(body), { ...init, headers })
}

function calledUrl(callIndex = 0): string {
  return String(fetchMock.mock.calls[callIndex][0])
}

function calledInit(callIndex = 0): RequestInit {
  return fetchMock.mock.calls[callIndex][1] as RequestInit
}

function calledHeaders(callIndex = 0): Headers {
  return new Headers(calledInit(callIndex).headers)
}

function calledBody(callIndex = 0): string {
  return new TextDecoder().decode(calledInit(callIndex).body as ArrayBuffer)
}

function setCookies(response: Response): string[] {
  const headers = response.headers as Headers & {
    getSetCookie?: () => string[]
  }
  return typeof headers.getSetCookie === 'function'
    ? headers.getSetCookie()
    : []
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Pages Function proxy request forwarding', () => {
  it('forwards GET method, path, query string, cookies, and auth headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    const response = await proxyRequest(context(
      '/api/v1/transactions?limit=25&type=expense',
      {
        headers: {
          Authorization: 'Bearer access-token',
          Cookie: 'finpath_refresh=refresh-value',
          'X-CSRF-Token': 'csrf-value',
          Origin: PAGES_ORIGIN,
          'Sec-Fetch-Site': 'same-origin',
        },
      },
    ))

    expect(response.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(calledUrl()).toBe(
      `${BACKEND}/api/v1/transactions?limit=25&type=expense`,
    )
    expect(calledInit().method).toBe('GET')
    expect(calledInit().body).toBeUndefined()

    const headers = calledHeaders()
    expect(headers.get('Authorization')).toBe('Bearer access-token')
    expect(headers.get('Cookie')).toBe('finpath_refresh=refresh-value')
    expect(headers.get('X-CSRF-Token')).toBe('csrf-value')
    expect(headers.get('Origin')).toBe(PAGES_ORIGIN)
    expect(headers.get('Sec-Fetch-Site')).toBe('same-origin')
  })

  it('forwards the request correlation ID and returns the backend value', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }, {
      headers: { 'X-Request-ID': 'backend-request-1' },
    }))

    const response = await proxyRequest(context('/api/v1/transactions', {
      headers: { 'X-Request-ID': 'client-request-1' },
    }))

    expect(calledHeaders().get('X-Request-ID')).toBe('client-request-1')
    expect(response.headers.get('X-Request-ID')).toBe('backend-request-1')
  })

  it('forwards POST bodies and idempotency or precondition headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: { id: 'tx-1' } }, {
      status: 201,
    }))

    const payload = {
      amount_kyat: 15_000,
      transaction_date: '2026-11-08',
      description: 'Lunch',
    }

    const response = await proxyRequest(context('/api/v1/transactions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'idem-key-1',
        'If-Match': '"3"',
        'If-None-Match': '"2"',
      },
      body: JSON.stringify(payload),
    }))

    expect(response.status).toBe(201)
    expect(calledInit().method).toBe('POST')
    expect(calledBody()).toBe(JSON.stringify(payload))
    expect(calledHeaders().get('Content-Type')).toBe('application/json')
    expect(calledHeaders().get('Idempotency-Key')).toBe('idem-key-1')
    expect(calledHeaders().get('If-Match')).toBe('"3"')
    expect(calledHeaders().get('If-None-Match')).toBe('"2"')
  })

  it.each(['PUT', 'PATCH', 'DELETE'])(
    'forwards %s requests and their bodies',
    async (method) => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

      const response = await proxyRequest(context('/api/v1/loans/loan-1', {
        method,
        body: '{"note":"updated"}',
        headers: { 'Content-Type': 'application/json' },
      }))

      expect(response.status).toBe(204)
      expect(calledInit().method).toBe(method)
      expect(calledBody()).toBe('{"note":"updated"}')
    },
  )

  it('does not forward client Host or X-Forwarded-* headers', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    await proxyRequest(context('/api/v1/transactions', {
      headers: {
        Host: 'attacker.example',
        'X-Forwarded-For': '203.0.113.10',
        'X-Forwarded-Host': 'attacker.example',
        'X-Forwarded-Proto': 'http',
        'X-Real-IP': '203.0.113.10',
      },
    }))

    const headers = calledHeaders()
    expect(headers.has('Host')).toBe(false)
    expect(headers.has('X-Forwarded-For')).toBe(false)
    expect(headers.has('X-Forwarded-Host')).toBe(false)
    expect(headers.has('X-Forwarded-Proto')).toBe(false)
    expect(headers.has('X-Real-IP')).toBe(false)
  })

  it('uses only the fixed backend URL, even with hostile query or header values', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    await proxyRequest(context(
      '/api/v1/transactions?next=https://attacker.example/steal',
      {
        headers: {
          'X-Forwarded-Host': 'attacker.example',
          'X-Forwarded-Proto': 'http',
        },
      },
    ))

    expect(calledUrl()).toBe(
      `${BACKEND}/api/v1/transactions?next=https://attacker.example/steal`,
    )
  })
})

describe('Pages Function proxy response forwarding', () => {
  it('preserves status, body, content type, location, and no-store caching', async () => {
    fetchMock.mockResolvedValue(new Response('{"data":{"ok":true}}', {
      status: 202,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
        Location: '/api/v1/auth/login',
      },
    }))

    const response = await proxyRequest(context('/api/v1/auth/refresh', {
      method: 'POST',
    }))

    expect(response.status).toBe(202)
    expect(await response.text()).toBe('{"data":{"ok":true}}')
    expect(response.headers.get('Content-Type')).toBe('application/json')
    expect(response.headers.get('Location')).toBe('/api/v1/auth/login')
    expect(response.headers.get('Cache-Control')).toBe('no-store')
  })

  it('preserves multiple Set-Cookie headers', async () => {
    const headers = new Headers()
    headers.append(
      'Set-Cookie',
      'finpath_refresh=abc; Path=/api/v1/auth; HttpOnly; Secure; SameSite=Lax',
    )
    headers.append(
      'Set-Cookie',
      'finpath_refresh_meta=def; Path=/api/v1/auth; HttpOnly; Secure; SameSite=Lax',
    )
    headers.set('Content-Type', 'application/json')

    fetchMock.mockResolvedValue(new Response('{"data":{}}', {
      status: 200,
      headers,
    }))

    const response = await proxyRequest(context('/api/v1/auth/login', {
      method: 'POST',
      body: '{"email":"a@example.test"}',
    }))

    expect(setCookies(response)).toEqual([
      'finpath_refresh=abc; Path=/api/v1/auth; HttpOnly; Secure; SameSite=Lax',
      'finpath_refresh_meta=def; Path=/api/v1/auth; HttpOnly; Secure; SameSite=Lax',
    ])
  })

  it('forces no-store when the upstream tries to make an API response cacheable', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }, {
      headers: { 'Cache-Control': 'public, max-age=3600' },
    }))

    const response = await proxyRequest(context('/api/v1/transactions'))

    expect(response.headers.get('Cache-Control')).toBe('no-store')
    expect(calledInit().cache).toBe('no-store')
    expect(calledInit().redirect).toBe('manual')
  })

  it('copies headers without folding Set-Cookie or leaking framing headers', () => {
    const headers = new Headers()
    headers.append('Set-Cookie', 'a=1')
    headers.append('Set-Cookie', 'b=2')
    headers.set('Content-Encoding', 'gzip')
    headers.set('Content-Length', '2')
    headers.set('X-Request-Id', 'request-1')

    const copied = copyUpstreamHeaders(new Response(null, { headers }))

    expect(copied.has('Content-Encoding')).toBe(false)
    expect(copied.has('Content-Length')).toBe(false)
    expect(copied.get('X-Request-Id')).toBe('request-1')
    expect(copied.get('Cache-Control')).toBe('no-store')
  })
})

describe('Pages Function proxy failures', () => {
  it('returns a controlled error when the backend URL is missing', async () => {
    const response = await proxyRequest(
      context('/api/v1/transactions', {}, { RENDER_BACKEND_URL: '' }),
    )

    expect(response.status).toBe(503)
    expect(response.headers.get('Cache-Control')).toBe('no-store')
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'proxy_backend_not_configured',
        message: expect.stringContaining('RENDER_BACKEND_URL'),
      },
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each(['not-a-url', 'ftp://backend.example', 'file:///tmp/backend'])(
    'rejects an invalid backend URL: %s',
    async (backendUrl) => {
      const response = await proxyRequest(
        context('/api/v1/transactions', {}, env(backendUrl)),
      )

      expect(response.status).toBe(503)
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )

  it('returns a controlled error when the upstream is unavailable', async () => {
    fetchMock.mockRejectedValue(new TypeError('network failure'))

    const response = await proxyRequest(context('/api/v1/transactions'))

    expect(response.status).toBe(502)
    expect(response.headers.get('Content-Type')).toContain('application/json')
    await expect(response.json()).resolves.toEqual({
      error: {
        code: 'proxy_upstream_unavailable',
        message: expect.any(String),
      },
    })
  })

  it('rejects unsupported methods without contacting the backend', async () => {
    const response = await proxyRequest(context('/api/v1/transactions', {
      method: 'PROPFIND',
    }))

    expect(response.status).toBe(405)
    expect(response.headers.get('Allow')).toContain('GET')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not log request bodies, cookies, authorization, or CSRF values', async () => {
    const spies = [
      vi.spyOn(console, 'log').mockImplementation(() => {}),
      vi.spyOn(console, 'info').mockImplementation(() => {}),
      vi.spyOn(console, 'warn').mockImplementation(() => {}),
      vi.spyOn(console, 'error').mockImplementation(() => {}),
      vi.spyOn(console, 'debug').mockImplementation(() => {}),
    ]
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    await proxyRequest(context('/api/v1/auth/login', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer secret-token',
        Cookie: 'finpath_refresh=secret-cookie',
        'X-CSRF-Token': 'secret-csrf',
      },
      body: '{"password":"secret-password"}',
    }))

    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled()
      spy.mockRestore()
    }
  })
})

describe('Pages Function proxy path handling', () => {
  it('preserves a configured backend path prefix', () => {
    const target = buildUpstreamUrl(
      new URL('https://backend.example/finpath/'),
      new URL(`${PAGES_ORIGIN}/api/v1/transactions?limit=5`),
    )

    expect(target.toString()).toBe(
      'https://backend.example/finpath/api/v1/transactions?limit=5',
    )
  })
})

describe('Pages Function entry point', () => {
  it('routes onRequest through the proxy', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    const response = await onRequest(context('/api/v1/auth/me', {
      headers: { Authorization: 'Bearer access-token' },
    }))

    expect(response.status).toBe(200)
    expect(calledUrl()).toBe(`${BACKEND}/api/v1/auth/me`)
  })
})
