import { isApiError } from './errors'

/**
 * Errors that may indicate a backend cold start or a temporary edge failure.
 * They are only retried for safe, idempotent requests such as `GET`.
 */
const COLD_START_STATUSES = new Set([502, 503, 504])

export function isColdStartFailure(error: unknown): boolean {
  if (!isApiError(error)) return false
  if (error.status === null) return true
  return COLD_START_STATUSES.has(error.status)
}

/**
 * The default budget for safe GET retries.
 *
 * A Render Free instance can take tens of seconds to wake from inactivity, so
 * the previous 250 ms + 750 ms budget was exhausted long before the backend was
 * able to answer. These delays keep the total wait bounded at roughly 15 s
 * while giving the instance enough time to start.
 */
export const SAFE_RETRY_ATTEMPTS = 5
export const SAFE_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000] as const

export type SafeRetryOptions = {
  attempts?: number
  delaysMs?: readonly number[]
  sleep?: (delayMs: number) => Promise<void>
}

function sleepWithTimeout(delayMs: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, delayMs)
  })
}

/**
 * Retry a request that is safe to repeat, such as `GET /auth/csrf`.
 *
 * Only network failures and 502/503/504 responses are retried. Authentication
 * failures (401/403), validation errors, and conflicts are returned
 * immediately. This helper must never be used for refresh-token POST requests,
 * because a lost refresh response may already have rotated the refresh token.
 */
export async function retrySafeRequest<T>(
  operation: () => Promise<T>,
  {
    attempts = SAFE_RETRY_ATTEMPTS,
    delaysMs = SAFE_RETRY_DELAYS_MS,
    sleep = sleepWithTimeout,
  }: SafeRetryOptions = {},
): Promise<T> {
  if (!Number.isInteger(attempts) || attempts < 1) {
    throw new Error('Safe retry attempts must be a positive integer.')
  }

  let lastError: unknown

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await operation()
    } catch (error) {
      lastError = error
      if (!isColdStartFailure(error) || attempt === attempts - 1) {
        throw error
      }

      const delay = delaysMs[Math.min(attempt, delaysMs.length - 1)] ?? 0
      if (delay > 0) {
        await sleep(delay)
      }
    }
  }

  throw lastError
}
