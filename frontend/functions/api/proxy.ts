/**
 * Same-origin API proxy for Cloudflare Pages Functions.
 *
 * The browser always calls `/api/*` on the Pages origin. This Function forwards
 * the request to one fixed backend supplied by `RENDER_BACKEND_URL`; the
 * destination is never taken from the request path, query string, or headers,
 * so the Function cannot be used as an open proxy.
 *
 * No request or response body, cookie, authorization header, CSRF token, or
 * secret is logged here.
 */

export type PagesFunctionEnv = {
  RENDER_BACKEND_URL?: string
}

export type PagesFunctionContext = {
  request: Request
  env: PagesFunctionEnv
}

const ALLOWED_METHODS = new Set([
  'GET',
  'HEAD',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'OPTIONS',
])

/**
 * A deliberate allowlist. Client-supplied hop-by-hop headers such as `Host`,
 * `Connection`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`,
 * and `X-Real-IP` are never forwarded.
 */
const FORWARDED_REQUEST_HEADERS = [
  'Accept',
  'Authorization',
  'Content-Type',
  'Cookie',
  'Idempotency-Key',
  'If-Match',
  'If-None-Match',
  'Origin',
  'Sec-Fetch-Site',
  'X-CSRF-Token',
  'X-Request-ID',
] as const

const RESPONSE_HEADERS_NOT_COPIED = new Set([
  // The Function returns a fresh body, so the upstream framing/encoding
  // headers may no longer describe it accurately.
  'connection',
  'content-encoding',
  'content-length',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

type ProxyErrorBody = {
  error: {
    code: string
    message: string
  }
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  extraHeaders: Record<string, string> = {},
): Response {
  const body: ProxyErrorBody = { error: { code, message } }
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      ...extraHeaders,
    },
  })
}

/**
 * Resolve the configured backend once per request. Only absolute HTTP(S) URLs
 * without embedded credentials are accepted.
 */
export function resolveBackendUrl(
  configuredBackendUrl: string | undefined,
): URL | null {
  const trimmed = configuredBackendUrl?.trim()
  if (!trimmed) return null

  let backendUrl: URL
  try {
    backendUrl = new URL(trimmed)
  } catch {
    return null
  }

  if (backendUrl.protocol !== 'https:' && backendUrl.protocol !== 'http:') {
    return null
  }
  if (backendUrl.username || backendUrl.password) {
    return null
  }

  return backendUrl
}

/**
 * Build the upstream URL from the fixed backend origin and the incoming
 * `/api/*` path. The query string is preserved verbatim.
 */
export function buildUpstreamUrl(backendUrl: URL, requestUrl: URL): URL {
  const basePath = backendUrl.pathname.replace(/\/+$/, '')
  const incomingPath = requestUrl.pathname.startsWith('/')
    ? requestUrl.pathname
    : `/${requestUrl.pathname}`

  const target = new URL(backendUrl.toString())
  target.pathname = `${basePath}${incomingPath}` || '/'
  target.search = requestUrl.search
  target.hash = ''
  return target
}

export function buildForwardHeaders(request: Request): Headers {
  const headers = new Headers()
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name)
    if (value !== null) {
      headers.set(name, value)
    }
  }
  return headers
}

function getSetCookieValues(headers: Headers): string[] {
  const candidate = headers as Headers & {
    getSetCookie?: () => string[]
    getAll?: (name: string) => string[]
  }

  if (typeof candidate.getSetCookie === 'function') {
    const values = candidate.getSetCookie()
    if (values.length > 0) return values
  }
  if (typeof candidate.getAll === 'function') {
    const values = candidate.getAll('Set-Cookie')
    if (values.length > 0) return values
  }

  const combined = headers.get('Set-Cookie')
  return combined ? [combined] : []
}

/**
 * Copy upstream headers without folding multiple `Set-Cookie` values.
 *
 * API responses are never cacheable: an upstream `no-store` value is kept, and
 * any other or missing `Cache-Control` is replaced with `no-store`.
 */
export function copyUpstreamHeaders(upstream: Response): Headers {
  const headers = new Headers()

  upstream.headers.forEach((value, name) => {
    const normalized = name.toLowerCase()
    if (normalized === 'set-cookie') return
    if (RESPONSE_HEADERS_NOT_COPIED.has(normalized)) return
    headers.append(name, value)
  })

  for (const cookie of getSetCookieValues(upstream.headers)) {
    headers.append('Set-Cookie', cookie)
  }

  const cacheControl = headers.get('Cache-Control')
  if (!cacheControl || !/\bno-store\b/i.test(cacheControl)) {
    headers.set('Cache-Control', 'no-store')
  }

  return headers
}

/**
 * Handle one Pages Function request. Returns a controlled JSON error instead
 * of throwing when configuration is missing or the upstream cannot be reached.
 */
export async function proxyRequest({
  request,
  env,
}: PagesFunctionContext): Promise<Response> {
  const method = request.method.toUpperCase()

  if (!ALLOWED_METHODS.has(method)) {
    return errorResponse(
      405,
      'proxy_method_not_allowed',
      'This request method is not supported by the API proxy.',
      { Allow: [...ALLOWED_METHODS].join(', ') },
    )
  }

  const backendUrl = resolveBackendUrl(env.RENDER_BACKEND_URL)
  if (!backendUrl) {
    return errorResponse(
      503,
      'proxy_backend_not_configured',
      'The API proxy is not configured. Set RENDER_BACKEND_URL for this Pages project.',
    )
  }

  const requestUrl = new URL(request.url)
  const upstreamUrl = buildUpstreamUrl(backendUrl, requestUrl)
  const headers = buildForwardHeaders(request)

  // Buffer JSON-sized request bodies so the same RequestInit works in Workers
  // and in the Node-based test environment. GET, HEAD, and OPTIONS never carry
  // a body.
  const body =
    method === 'GET' || method === 'HEAD' || method === 'OPTIONS'
      ? undefined
      : await request.arrayBuffer()

  let upstream: Response
  try {
    upstream = await fetch(upstreamUrl.toString(), {
      method,
      headers,
      body,
      redirect: 'manual',
      cache: 'no-store',
    })
  } catch {
    return errorResponse(
      502,
      'proxy_upstream_unavailable',
      'The API backend is temporarily unavailable. Please try again.',
    )
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: copyUpstreamHeaders(upstream),
  })
}
