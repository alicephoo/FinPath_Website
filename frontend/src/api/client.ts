import { buildApiUrl } from './config'
import { ApiError, type ApiFieldError } from './errors'

export type ApiMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export type ApiRequestOptions = {
  method?: ApiMethod
  body?: unknown
  headers?: HeadersInit
  accessToken?: string | null
  credentials?: RequestCredentials
  csrfToken?: string | null
  signal?: AbortSignal
}

/**
 * The parsed response body. `data` is `undefined` for empty responses and is a
 * string for successful non-JSON responses such as a future CSV export.
 *
 * `requestId` is the validated `X-Request-ID` returned by the backend, when
 * present. It exists for correlation only and never carries user or financial
 * data.
 */
export type ApiResponse<T> = {
  data: T | undefined
  status: number
  headers: Headers
  requestId?: string
}

const REQUEST_ID_HEADER = 'X-Request-ID'

/**
 * Mirrors the backend contract in `backend/app/core/observability.py`:
 * 1–128 characters, the first alphanumeric, the remainder limited to
 * alphanumerics and `.`, `_`, `:` or `-`. Response IDs that do not match are
 * ignored rather than surfaced.
 */
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/

/**
 * Generate a per-request correlation ID using the platform cryptographic UUID
 * generator. Returns `undefined` when it is unavailable, in which case no
 * header is sent and the backend generates its own ID. `Math.random()` is
 * never used for this purpose.
 */
function generateRequestId(): string | undefined {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID()
  }
  return undefined
}

/**
 * Read and validate the `X-Request-ID` response header. Missing or malformed
 * values are treated as absent so callers never receive an untrusted string.
 */
function readResponseRequestId(headers: Headers): string | undefined {
  const value = headers.get(REQUEST_ID_HEADER)
  if (value === null) return undefined

  const trimmed = value.trim()
  return REQUEST_ID_PATTERN.test(trimmed) ? trimmed : undefined
}

/**
 * Resolve the correlation ID to send with an outgoing request. A valid
 * caller-supplied `X-Request-ID` is reused; otherwise a fresh ID is generated.
 * Malformed caller values are never sent or exposed as correlation IDs.
 */
function resolveOutgoingRequestId(headers: Headers): string | undefined {
  const provided = headers.get(REQUEST_ID_HEADER)
  if (provided !== null) {
    const trimmed = provided.trim()
    if (REQUEST_ID_PATTERN.test(trimmed)) return trimmed
  }
  return generateRequestId()
}

type MethodOptions = Omit<ApiRequestOptions, 'method' | 'body'>

type ParsedErrorEnvelope = {
  code: string
  message?: string
  fieldErrors: ApiFieldError[]
  requestId?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isAbortError(error: unknown): boolean {
  return isRecord(error) && error.name === 'AbortError'
}

function parseFieldErrors(value: unknown): ApiFieldError[] {
  if (!Array.isArray(value)) return []

  return value.flatMap((item) => {
    if (!isRecord(item)) return []
    return [{
      field: typeof item.field === 'string' ? item.field : '',
      code: typeof item.code === 'string' ? item.code : '',
      message: typeof item.message === 'string' ? item.message : '',
    }]
  })
}

function parseErrorEnvelope(payload: unknown): ParsedErrorEnvelope | null {
  if (!isRecord(payload) || !isRecord(payload.error)) return null

  const error = payload.error
  const hasCode = typeof error.code === 'string'
  const hasMessage = typeof error.message === 'string'
  if (!hasCode && !hasMessage) return null

  return {
    code: hasCode ? error.code as string : 'http_error',
    message: hasMessage ? error.message as string : undefined,
    fieldErrors: parseFieldErrors(error.field_errors),
    requestId: typeof error.request_id === 'string'
      ? error.request_id
      : undefined,
  }
}

function tryParseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

function isJsonResponse(response: Response): boolean {
  const contentType = response.headers.get('content-type')?.toLowerCase() ?? ''
  return contentType.includes('application/json')
    || contentType.includes('+json')
}

function parseSuccessBody<T>(response: Response, text: string): T | undefined {
  if (!text) return undefined

  if (!isJsonResponse(response)) {
    return text as T
  }

  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError({
      status: response.status,
      code: 'invalid_json_response',
      message: 'The server returned an invalid JSON response.',
      requestId: readResponseRequestId(response.headers),
      headers: response.headers,
    })
  }
}

function parseHttpError(response: Response, text: string): ApiError {
  const envelope = parseErrorEnvelope(text ? tryParseJson(text) : undefined)
  const fallbackMessage = response.statusText
    || `Request failed with status ${response.status}.`

  return new ApiError({
    status: response.status,
    code: envelope?.code ?? 'http_error',
    message: envelope?.message ?? fallbackMessage,
    fieldErrors: envelope?.fieldErrors,
    // Prefer the response header, then fall back to the error envelope.
    requestId: readResponseRequestId(response.headers) ?? envelope?.requestId,
    headers: response.headers,
  })
}

/**
 * Send a request to the FinPath API. The path is relative to
 * `VITE_API_BASE_URL`; for example, `/transactions` becomes
 * `http://localhost:8000/api/v1/transactions` locally.
 *
 * Authentication, idempotency, and concurrency headers are supplied by the
 * caller. This function does not store tokens, generate idempotency keys,
 * retry requests, or refresh sessions.
 */
export async function apiRequest<T>(
  path: string,
  {
    method = 'GET',
    body,
    headers: customHeaders,
    accessToken,
    credentials,
    csrfToken,
    signal,
  }: ApiRequestOptions = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(customHeaders)

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  const outgoingRequestId = resolveOutgoingRequestId(headers)
  if (outgoingRequestId !== undefined) {
    headers.set(REQUEST_ID_HEADER, outgoingRequestId)
  }
  if (accessToken && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  if (csrfToken && !headers.has('X-CSRF-Token')) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  if (body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const requestInit: RequestInit = { method, headers, signal }
  if (credentials !== undefined) {
    requestInit.credentials = credentials
  }
  if (body !== undefined) {
    requestInit.body = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(buildApiUrl(path), requestInit)
  } catch (error) {
    if (isAbortError(error)) throw error
    throw new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API. Check your connection and try again.',
      requestId: outgoingRequestId,
    })
  }

  const text = await response.text()

  if (!response.ok) {
    throw parseHttpError(response, text)
  }

  return {
    data: parseSuccessBody<T>(response, text),
    status: response.status,
    headers: response.headers,
    requestId: readResponseRequestId(response.headers),
  }
}

export const apiClient = {
  get<T>(path: string, options: MethodOptions = {}): Promise<ApiResponse<T>> {
    return apiRequest<T>(path, { ...options, method: 'GET' })
  },

  post<T>(
    path: string,
    body?: unknown,
    options: MethodOptions = {},
  ): Promise<ApiResponse<T>> {
    return apiRequest<T>(path, { ...options, method: 'POST', body })
  },

  put<T>(
    path: string,
    body?: unknown,
    options: MethodOptions = {},
  ): Promise<ApiResponse<T>> {
    return apiRequest<T>(path, { ...options, method: 'PUT', body })
  },

  patch<T>(
    path: string,
    body?: unknown,
    options: MethodOptions = {},
  ): Promise<ApiResponse<T>> {
    return apiRequest<T>(path, { ...options, method: 'PATCH', body })
  },

  delete<T = void>(
    path: string,
    options: MethodOptions = {},
  ): Promise<ApiResponse<T>> {
    return apiRequest<T>(path, { ...options, method: 'DELETE' })
  },
}
