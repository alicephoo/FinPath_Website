import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import { isColdStartFailure, retrySafeRequest } from './retry'

function apiError(status: number | null, code = 'http_error'): ApiError {
  return new ApiError({
    status,
    code,
    message: 'Request failed.',
  })
}

describe('cold-start classification', () => {
  it.each([502, 503, 504])('treats HTTP %s as recoverable', (status) => {
    expect(isColdStartFailure(apiError(status))).toBe(true)
  })

  it('treats a network failure as recoverable', () => {
    expect(isColdStartFailure(apiError(null, 'network_error'))).toBe(true)
  })

  it.each([400, 401, 403, 409, 412, 422, 500])(
    'does not treat HTTP %s as a cold-start failure',
    (status) => {
      expect(isColdStartFailure(apiError(status))).toBe(false)
    },
  )

  it('does not treat unrelated errors as cold-start failures', () => {
    expect(isColdStartFailure(new Error('boom'))).toBe(false)
    expect(isColdStartFailure('boom')).toBe(false)
  })
})

describe('safe request retry', () => {
  it('returns immediately when the first attempt succeeds', async () => {
    const operation = vi.fn(async () => 'ok')
    const sleep = vi.fn(async () => {})

    await expect(retrySafeRequest(operation, { sleep })).resolves.toBe('ok')
    expect(operation).toHaveBeenCalledTimes(1)
    expect(sleep).not.toHaveBeenCalled()
  })

  it('retries 503 responses up to three attempts', async () => {
    const operation = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(apiError(503))
      .mockRejectedValueOnce(apiError(502))
      .mockResolvedValueOnce('ok')
    const sleep = vi.fn(async () => {})

    await expect(retrySafeRequest(operation, { sleep })).resolves.toBe('ok')
    expect(operation).toHaveBeenCalledTimes(3)
    expect(sleep.mock.calls).toEqual([[250], [750]])
  })

  it('retries network failures', async () => {
    const operation = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(apiError(null, 'network_error'))
      .mockResolvedValueOnce('ok')
    const sleep = vi.fn(async () => {})

    await expect(retrySafeRequest(operation, { sleep })).resolves.toBe('ok')
    expect(operation).toHaveBeenCalledTimes(2)
  })

  it.each([401, 403, 409, 412, 422, 500])(
    'does not retry HTTP %s',
    async (status) => {
      const operation = vi.fn<() => Promise<string>>()
        .mockRejectedValue(apiError(status))
      const sleep = vi.fn(async () => {})

      await expect(retrySafeRequest(operation, { sleep })).rejects.toMatchObject({
        status,
      })
      expect(operation).toHaveBeenCalledTimes(1)
      expect(sleep).not.toHaveBeenCalled()
    },
  )

  it('rethrows the final error after exhausting attempts', async () => {
    const operation = vi.fn<() => Promise<string>>()
      .mockRejectedValue(apiError(503, 'proxy_upstream_unavailable'))
    const sleep = vi.fn(async () => {})

    await expect(retrySafeRequest(operation, {
      attempts: 2,
      sleep,
    })).rejects.toMatchObject({
      status: 503,
      code: 'proxy_upstream_unavailable',
    })
    expect(operation).toHaveBeenCalledTimes(2)
    expect(sleep).toHaveBeenCalledTimes(1)
  })

  it('rejects an invalid attempt count', async () => {
    const operation = vi.fn(async () => 'ok')

    await expect(retrySafeRequest(operation, { attempts: 0 }))
      .rejects.toThrow('positive integer')
    expect(operation).not.toHaveBeenCalled()
  })
})
