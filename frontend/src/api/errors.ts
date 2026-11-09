export type ApiFieldError = {
  field: string
  code: string
  message: string
}

export type ApiErrorOptions = {
  status: number | null
  code: string
  message: string
  fieldErrors?: ApiFieldError[]
  requestId?: string
  headers?: Headers
}

/**
 * A typed error for both HTTP failures and network failures.
 *
 * `status` is `null` for a network failure, so callers can distinguish a
 * failed connection from an HTTP response such as 401, 422, or 503.
 */
export class ApiError extends Error {
  readonly status: number | null
  readonly code: string
  readonly fieldErrors: ApiFieldError[]
  readonly requestId?: string
  readonly headers: Headers
  readonly isNetworkError: boolean

  constructor({
    status,
    code,
    message,
    fieldErrors = [],
    requestId,
    headers = new Headers(),
  }: ApiErrorOptions) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
    this.requestId = requestId
    this.headers = headers
    this.isNetworkError = status === null
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}
