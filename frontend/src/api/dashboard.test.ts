import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from './config'
import {
  fetchDashboardOverview,
  type DashboardOverview,
} from './dashboard'
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

const overview: DashboardOverview = {
  account_balance: {
    status: 'configured',
    starting_balance_kyat: 800_000,
    effective_date: '2026-10-31',
    amount_kyat: 825_000,
    as_of_date: '2026-11-03',
  },
  all_time_income_kyat: 2_500_000,
  all_time_expense_kyat: 1_000_000,
  travel_expense_kyat: 0,
  total_expense_kyat: 1_000_000,
  current_month_income_kyat: 400_000,
  current_month_expense_kyat: 150_000,
  outstanding_loan_principal_kyat: 50_000_000,
  active_loan_count: 2,
  loan_repayments_kyat: 5_000_000,
  actual_savings_balance_kyat: 300_000,
  savings_target_kyat: 1_000_000,
  active_savings_goal_count: 1,
  savings_goals: [],
  recent_transactions: [],
}

describe('dashboard overview API', () => {
  it('loads the owner-scoped overview with the default recent limit', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: overview }))

    const result = await fetchDashboardOverview('access-token-1')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/dashboard/overview?recent_limit=5`,
    )
    expect(requestInit().method).toBe('GET')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual(overview)
  })

  it('passes an explicit valid recent limit', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: overview }))

    await fetchDashboardOverview('access-token-1', 10)

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/dashboard/overview?recent_limit=10`,
    )
  })

  it.each([0, 51, 2.5, Number.NaN])(
    'falls back to the default limit for invalid value %s',
    async (limit) => {
      fetchMock.mockResolvedValue(jsonResponse({ data: overview }))

      await fetchDashboardOverview('access-token-1', limit)

      expect(fetchMock.mock.calls[0][0]).toBe(
        `${API_BASE_URL}/dashboard/overview?recent_limit=5`,
      )
    },
  )

  it('rejects an unexpected response shape', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: null }))

    const error = await captureApiError(fetchDashboardOverview('access-token-1'))

    expect(error.code).toBe('invalid_response')
    expect(error.status).toBe(200)
  })

  it('propagates authenticated request failures', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'authentication_required',
        message: 'Authentication is required.',
        field_errors: [],
        request_id: 'request-test',
      },
    }, { status: 401 }))

    const error = await captureApiError(fetchDashboardOverview('access-token-1'))

    expect(error.status).toBe(401)
    expect(error.code).toBe('authentication_required')
  })
})
