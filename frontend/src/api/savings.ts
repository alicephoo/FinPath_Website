import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'

export type SavingsGoalStatus = 'active' | 'archived'

/**
 * Savings goal resource returned by list, read, create, and update. The backend
 * derives `actual_balance_kyat` from append-only entries and `remaining_kyat`
 * from the target minus the actual balance; both are authoritative.
 */
export type SavingsGoalResource = {
  id: string
  name: string
  target_kyat: number
  tone: string | null
  status: SavingsGoalStatus
  actual_balance_kyat: number
  remaining_kyat: number
  created_at: string
  updated_at: string
}

export type SavingsGoalCreateInput = {
  name: string
  target_kyat: number
  tone?: string | null
}

/** Partial-update body accepted by PATCH; name and target may not be null. */
export type SavingsGoalUpdateInput = {
  name?: string
  target_kyat?: number
  tone?: string | null
}

export type SavingsEntryType = 'contribution' | 'withdrawal'

/**
 * Append-only savings entry. Contributions and withdrawals can be created
 * through this client; corrections have no backend route.
 */
export type SavingsEntryResource = {
  id: string
  entry_type: SavingsEntryType
  amount_kyat: number
  entry_date: string
  note: string | null
  plan_day_id: string | null
  correction_of_entry_id: string | null
  correction_reason: string | null
  created_at: string
}

export type SavingsContributionCreateInput = {
  amount_kyat: number
  entry_date: string
  note?: string | null
}

/** Withdrawals use the same validated entry fields as contributions. */
export type SavingsWithdrawalCreateInput = SavingsContributionCreateInput

export type SavingsGoalView = {
  goal: SavingsGoalResource
  etag: string | null
}

type Envelope<T> = {
  data: T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function invalidResponse(
  response: ApiResponse<unknown>,
  message: string,
): ApiError {
  return new ApiError({
    status: response.status,
    code: 'invalid_response',
    message,
    headers: response.headers,
  })
}

function isSavingsGoalResource(value: unknown): value is SavingsGoalResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.target_kyat === 'number'
    && (value.tone === null || typeof value.tone === 'string')
    && (value.status === 'active' || value.status === 'archived')
    && typeof value.actual_balance_kyat === 'number'
    && typeof value.remaining_kyat === 'number'
    && typeof value.created_at === 'string'
    && typeof value.updated_at === 'string'
}

function isSavingsEntryResource(value: unknown): value is SavingsEntryResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && (value.entry_type === 'contribution' || value.entry_type === 'withdrawal')
    && typeof value.amount_kyat === 'number'
    && typeof value.entry_date === 'string'
    && (value.note === null || typeof value.note === 'string')
    && (value.plan_day_id === null || typeof value.plan_day_id === 'string')
    && (value.correction_of_entry_id === null
      || typeof value.correction_of_entry_id === 'string')
    && (value.correction_reason === null
      || typeof value.correction_reason === 'string')
    && typeof value.created_at === 'string'
}

function unwrapGoal(
  response: ApiResponse<Envelope<SavingsGoalResource>>,
): SavingsGoalResource {
  const data = response.data?.data
  if (!isSavingsGoalResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected savings goal response.',
    )
  }
  return data
}

function unwrapEntry(
  response: ApiResponse<Envelope<SavingsEntryResource>>,
): SavingsEntryResource {
  const data = response.data?.data
  if (!isSavingsEntryResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected savings entry response.',
    )
  }
  return data
}

/**
 * List caller-owned savings goals. The backend defaults to `active`; archived
 * history is available only by explicitly requesting it.
 */
export async function fetchSavingsGoals(
  accessToken: string,
  status: 'active' | 'archived' | 'all' = 'active',
): Promise<SavingsGoalResource[]> {
  const response = await apiClient.get<Envelope<SavingsGoalResource[]>>(
    `/savings/goals?status=${status}`,
    { accessToken },
  )
  const goals = response.data?.data
  if (!Array.isArray(goals) || !goals.every(isSavingsGoalResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected savings goal list response.',
    )
  }
  return goals
}

/** Read one goal and its current ETag for a later PATCH. */
export async function fetchSavingsGoal(
  accessToken: string,
  goalId: string,
): Promise<SavingsGoalView> {
  const response = await apiClient.get<Envelope<SavingsGoalResource>>(
    `/savings/goals/${encodeURIComponent(goalId)}`,
    { accessToken },
  )
  return {
    goal: unwrapGoal(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Create a savings goal. The caller supplies the idempotency key so the same
 * key can be reused when retrying the same unchanged operation.
 */
export async function createSavingsGoal(
  accessToken: string,
  input: SavingsGoalCreateInput,
  idempotencyKey: string,
): Promise<SavingsGoalView> {
  const response = await apiClient.post<Envelope<SavingsGoalResource>>(
    '/savings/goals',
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    goal: unwrapGoal(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Partially update a goal. The current ETag must be supplied so stale writes
 * are rejected with `412 stale_revision`.
 */
export async function updateSavingsGoal(
  accessToken: string,
  goalId: string,
  input: SavingsGoalUpdateInput,
  etag: string,
): Promise<SavingsGoalView> {
  const response = await apiClient.patch<Envelope<SavingsGoalResource>>(
    `/savings/goals/${encodeURIComponent(goalId)}`,
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    goal: unwrapGoal(response),
    etag: response.headers.get('ETag'),
  }
}

/** List the append-only entries recorded for one caller-owned goal. */
export async function fetchSavingsEntries(
  accessToken: string,
  goalId: string,
): Promise<SavingsEntryResource[]> {
  const response = await apiClient.get<Envelope<SavingsEntryResource[]>>(
    `/savings/goals/${encodeURIComponent(goalId)}/entries`,
    { accessToken },
  )
  const entries = response.data?.data
  if (!Array.isArray(entries) || !entries.every(isSavingsEntryResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected savings entry list response.',
    )
  }
  return entries
}

/**
 * Append a contribution to an active goal. The caller owns idempotency-key
 * reuse for retries of the same unchanged operation. This endpoint does not
 * return an ETag; re-read the goal for its current revision.
 */
export async function createSavingsContribution(
  accessToken: string,
  goalId: string,
  input: SavingsContributionCreateInput,
  idempotencyKey: string,
): Promise<SavingsEntryResource> {
  const response = await apiClient.post<Envelope<SavingsEntryResource>>(
    `/savings/goals/${encodeURIComponent(goalId)}/entries/contributions`,
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return unwrapEntry(response)
}

/**
 * Append a withdrawal to an active goal. The backend rejects a withdrawal
 * above the current actual balance with `409 withdrawal_exceeds_balance`.
 */
export async function createSavingsWithdrawal(
  accessToken: string,
  goalId: string,
  input: SavingsWithdrawalCreateInput,
  idempotencyKey: string,
): Promise<SavingsEntryResource> {
  const response = await apiClient.post<Envelope<SavingsEntryResource>>(
    `/savings/goals/${encodeURIComponent(goalId)}/entries/withdrawals`,
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return unwrapEntry(response)
}
