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
 */
export type ApiResponse<T> = {
  data: T | undefined
  status: number
  headers: Headers
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
    requestId: envelope?.requestId,
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
