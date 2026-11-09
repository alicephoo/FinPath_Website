import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import {
  createSavingsContribution,
  createSavingsGoal,
  createSavingsWithdrawal,
  fetchSavingsEntries,
  fetchSavingsGoal,
  fetchSavingsGoals,
  updateSavingsGoal,
  type SavingsEntryResource,
  type SavingsGoalCreateInput,
  type SavingsGoalResource,
} from './savings'

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

function requestUrl(callIndex = 0): URL {
  return new URL(String(fetchMock.mock.calls[callIndex][0]))
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

const goal: SavingsGoalResource = {
  id: 'goal-1',
  name: 'Rainy day fund',
  target_kyat: 12_000_000,
  tone: 'mint',
  status: 'active',
  actual_balance_kyat: 8_400_000,
  remaining_kyat: 3_600_000,
  created_at: '2026-10-01T09:00:00+00:00',
  updated_at: '2026-11-03T09:00:00+00:00',
}

const contribution: SavingsEntryResource = {
  id: 'entry-1',
  entry_type: 'contribution',
  amount_kyat: 100_000,
  entry_date: '2026-11-03',
  note: 'Monthly saving',
  plan_day_id: null,
  correction_of_entry_id: null,
  correction_reason: null,
  created_at: '2026-11-03T09:00:00+00:00',
}

const createInput: SavingsGoalCreateInput = {
  name: 'Rainy day fund',
  target_kyat: 12_000_000,
  tone: 'mint',
}

describe('savings API', () => {
  it('lists active goals by default with the caller token', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [goal] }))

    const result = await fetchSavingsGoals('access-token-1')

    const url = requestUrl()
    expect(url.pathname).toBe('/api/v1/savings/goals')
    expect(url.searchParams.get('status')).toBe('active')
    expect(requestInit().method).toBe('GET')
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual([goal])
  })

  it('requests archived goals explicitly when asked', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [] }))

    await fetchSavingsGoals('access-token-1', 'archived')

    expect(requestUrl().searchParams.get('status')).toBe('archived')
  })

  it('reads one goal and exposes its ETag', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: goal },
      { headers: { ETag: '"4"' } },
    ))

    const result = await fetchSavingsGoal('access-token-1', goal.id)

    expect(requestUrl().pathname).toBe(`/api/v1/savings/goals/${goal.id}`)
    expect(result.goal).toEqual(goal)
    expect(result.etag).toBe('"4"')
  })

  it('creates a goal with an idempotency key', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: goal },
      { status: 201, headers: { ETag: '"1"' } },
    ))

    const result = await createSavingsGoal(
      'access-token-1',
      createInput,
      'idempotency-key-goal-1',
    )

    expect(requestUrl().pathname).toBe('/api/v1/savings/goals')
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-goal-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual(createInput)
    expect(result.goal).toEqual(goal)
    expect(result.etag).toBe('"1"')
  })

  it('updates a goal with the current If-Match ETag', async () => {
    const updated = { ...goal, name: 'Updated rainy day fund', tone: 'gold' }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: updated },
      { headers: { ETag: '"5"' } },
    ))

    const result = await updateSavingsGoal(
      'access-token-1',
      goal.id,
      { name: 'Updated rainy day fund', tone: 'gold' },
      '"4"',
    )

    expect(requestUrl().pathname).toBe(`/api/v1/savings/goals/${goal.id}`)
    expect(requestInit().method).toBe('PATCH')
    expect(requestHeaders().get('If-Match')).toBe('"4"')
    expect(JSON.parse(String(requestInit().body))).toEqual({
      name: 'Updated rainy day fund',
      tone: 'gold',
    })
    expect(result.etag).toBe('"5"')
  })

  it('propagates a stale-revision goal update error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'stale_revision',
        message: 'The supplied revision does not match the current savings goal.',
        field_errors: [],
      },
    }, { status: 412 }))

    const error = await captureApiError(updateSavingsGoal(
      'access-token-1',
      goal.id,
      { name: 'Changed' },
      '"3"',
    ))

    expect(error.status).toBe(412)
    expect(error.code).toBe('stale_revision')
  })

  it('loads a goal contribution history', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ data: [contribution] }))

    const result = await fetchSavingsEntries('access-token-1', goal.id)

    expect(requestUrl().pathname).toBe(
      `/api/v1/savings/goals/${goal.id}/entries`,
    )
    expect(requestHeaders().get('Authorization')).toBe('Bearer access-token-1')
    expect(result).toEqual([contribution])
  })

  it('records a contribution with an idempotency key', async () => {
    fetchMock.mockResolvedValue(jsonResponse(
      { data: contribution },
      { status: 201 },
    ))

    const result = await createSavingsContribution(
      'access-token-1',
      goal.id,
      {
        amount_kyat: 100_000,
        entry_date: '2026-11-03',
        note: 'Monthly saving',
      },
      'idempotency-key-contribution-1',
    )

    expect(requestUrl().pathname).toBe(
      `/api/v1/savings/goals/${goal.id}/entries/contributions`,
    )
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-contribution-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual({
      amount_kyat: 100_000,
      entry_date: '2026-11-03',
      note: 'Monthly saving',
    })
    expect(result).toEqual(contribution)
  })

  it('propagates an archived-goal contribution error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'goal_archived',
        message: 'This savings goal is archived and cannot accept changes.',
        field_errors: [],
      },
    }, { status: 409 }))

    const error = await captureApiError(createSavingsContribution(
      'access-token-1',
      goal.id,
      { amount_kyat: 1_000, entry_date: '2026-11-03' },
      'idempotency-key-contribution-2',
    ))

    expect(error.status).toBe(409)
    expect(error.code).toBe('goal_archived')
  })

  it('records a withdrawal with an idempotency key', async () => {
    const withdrawal: SavingsEntryResource = {
      ...contribution,
      id: 'entry-withdrawal',
      entry_type: 'withdrawal',
      amount_kyat: 20_000,
      note: 'Cash out',
    }
    fetchMock.mockResolvedValue(jsonResponse(
      { data: withdrawal },
      { status: 201 },
    ))

    const result = await createSavingsWithdrawal(
      'access-token-1',
      goal.id,
      {
        amount_kyat: 20_000,
        entry_date: '2026-11-04',
        note: 'Cash out',
      },
      'idempotency-key-withdrawal-1',
    )

    expect(requestUrl().pathname).toBe(
      `/api/v1/savings/goals/${goal.id}/entries/withdrawals`,
    )
    expect(requestInit().method).toBe('POST')
    expect(requestHeaders().get('Idempotency-Key')).toBe(
      'idempotency-key-withdrawal-1',
    )
    expect(JSON.parse(String(requestInit().body))).toEqual({
      amount_kyat: 20_000,
      entry_date: '2026-11-04',
      note: 'Cash out',
    })
    expect(result).toEqual(withdrawal)
  })

  it('propagates an insufficient-balance withdrawal error', async () => {
    fetchMock.mockResolvedValue(jsonResponse({
      error: {
        code: 'withdrawal_exceeds_balance',
        message: 'The withdrawal exceeds the available savings balance.',
        field_errors: [],
      },
    }, { status: 409 }))

    const error = await captureApiError(createSavingsWithdrawal(
      'access-token-1',
      goal.id,
      { amount_kyat: 9_000_000, entry_date: '2026-11-04' },
      'idempotency-key-withdrawal-2',
    ))

    expect(error.status).toBe(409)
    expect(error.code).toBe('withdrawal_exceeds_balance')
  })

  it('rejects unexpected goal and entry response shapes', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: 'bad' }] }))
    expect((await captureApiError(fetchSavingsGoals('access-token-1'))).code)
      .toBe('invalid_response')

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: 'bad' }] }))
    expect((await captureApiError(
      fetchSavingsEntries('access-token-1', goal.id),
    )).code).toBe('invalid_response')

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: { amount_kyat: 1 } }))
    expect((await captureApiError(
      createSavingsContribution(
        'access-token-1',
        goal.id,
        { amount_kyat: 1_000, entry_date: '2026-11-03' },
        'key',
      ),
    )).code).toBe('invalid_response')
  })
})
