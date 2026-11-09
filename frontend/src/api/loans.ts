import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'

/**
 * Loan resource returned by list, read, create, and update. The backend derives
 * `total_repaid_kyat` and `outstanding_principal_kyat`; the frontend must use
 * those values rather than recomputing a balance from partial repayment data.
 */
export type LoanResource = {
  id: string
  name: string
  principal_kyat: number
  start_date: string
  total_repaid_kyat: number
  outstanding_principal_kyat: number
  created_at: string
  updated_at: string
}

export type LoanCreateInput = {
  name: string
  principal_kyat: number
  start_date: string
}

/** Partial-update body accepted by PATCH; fields may be omitted but not null. */
export type LoanUpdateInput = Partial<LoanCreateInput>

export type RepaymentResource = {
  id: string
  amount_kyat: number
  repayment_date: string
  note: string | null
  created_at: string
  /** True once an append-only reversal correction exists for this repayment. */
  corrected: boolean
}

export type RepaymentCreateInput = {
  amount_kyat: number
  repayment_date: string
  note?: string | null
}

export type CorrectionResource = {
  id: string
  original_repayment_id: string
  amount_kyat: number
  type: 'reversal'
  reason: string
  created_by_user_id: string
  created_at: string
}

export type RepaymentCorrectionResult = {
  original_repayment: RepaymentResource
  correction: CorrectionResource
  outstanding_principal_kyat: number
}

export type LoanView = {
  loan: LoanResource
  etag: string | null
}

export type RepaymentView = {
  repayment: RepaymentResource
  etag: string | null
}

export type RepaymentCorrectionView = {
  result: RepaymentCorrectionResult
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

function isLoanResource(value: unknown): value is LoanResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.principal_kyat === 'number'
    && typeof value.start_date === 'string'
    && typeof value.total_repaid_kyat === 'number'
    && typeof value.outstanding_principal_kyat === 'number'
    && typeof value.created_at === 'string'
    && typeof value.updated_at === 'string'
}

function isRepaymentResource(value: unknown): value is RepaymentResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.amount_kyat === 'number'
    && typeof value.repayment_date === 'string'
    && (value.note === null || typeof value.note === 'string')
    && typeof value.created_at === 'string'
    && typeof value.corrected === 'boolean'
}

function isCorrectionResource(value: unknown): value is CorrectionResource {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.original_repayment_id === 'string'
    && typeof value.amount_kyat === 'number'
    && value.type === 'reversal'
    && typeof value.reason === 'string'
    && typeof value.created_by_user_id === 'string'
    && typeof value.created_at === 'string'
}

function unwrapLoan(
  response: ApiResponse<Envelope<LoanResource>>,
): LoanResource {
  const data = response.data?.data
  if (!isLoanResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected loan response.',
    )
  }
  return data
}

function unwrapRepayment(
  response: ApiResponse<Envelope<RepaymentResource>>,
): RepaymentResource {
  const data = response.data?.data
  if (!isRepaymentResource(data)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected repayment response.',
    )
  }
  return data
}

/** List all caller-owned loans. Collection responses do not include ETags. */
export async function fetchLoans(accessToken: string): Promise<LoanResource[]> {
  const response = await apiClient.get<Envelope<LoanResource[]>>(
    '/loans',
    { accessToken },
  )
  const loans = response.data?.data
  if (!Array.isArray(loans) || !loans.every(isLoanResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected loan list response.',
    )
  }
  return loans
}

/** Read one loan and its current ETag for a later PATCH. */
export async function fetchLoan(
  accessToken: string,
  loanId: string,
): Promise<LoanView> {
  const response = await apiClient.get<Envelope<LoanResource>>(
    `/loans/${encodeURIComponent(loanId)}`,
    { accessToken },
  )
  return {
    loan: unwrapLoan(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Create a loan. The caller supplies the idempotency key so it can reuse the
 * same key when retrying the same unchanged operation.
 */
export async function createLoan(
  accessToken: string,
  input: LoanCreateInput,
  idempotencyKey: string,
): Promise<LoanView> {
  const response = await apiClient.post<Envelope<LoanResource>>(
    '/loans',
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    loan: unwrapLoan(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Partially update a loan. The current ETag must be supplied so stale writes
 * are rejected with `412 stale_revision`.
 */
export async function updateLoan(
  accessToken: string,
  loanId: string,
  input: LoanUpdateInput,
  etag: string,
): Promise<LoanView> {
  const response = await apiClient.patch<Envelope<LoanResource>>(
    `/loans/${encodeURIComponent(loanId)}`,
    input,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    loan: unwrapLoan(response),
    etag: response.headers.get('ETag'),
  }
}

/** List the repayment history for one caller-owned loan. */
export async function fetchRepayments(
  accessToken: string,
  loanId: string,
): Promise<RepaymentResource[]> {
  const response = await apiClient.get<Envelope<RepaymentResource[]>>(
    `/loans/${encodeURIComponent(loanId)}/repayments`,
    { accessToken },
  )
  const repayments = response.data?.data
  if (!Array.isArray(repayments) || !repayments.every(isRepaymentResource)) {
    throw invalidResponse(
      response,
      'The server returned an unexpected repayment list response.',
    )
  }
  return repayments
}

/**
 * Record a principal-only repayment. The caller owns idempotency-key reuse for
 * retries of the same unchanged operation.
 */
export async function createRepayment(
  accessToken: string,
  loanId: string,
  input: RepaymentCreateInput,
  idempotencyKey: string,
): Promise<RepaymentView> {
  const response = await apiClient.post<Envelope<RepaymentResource>>(
    `/loans/${encodeURIComponent(loanId)}/repayments`,
    input,
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  return {
    repayment: unwrapRepayment(response),
    etag: response.headers.get('ETag'),
  }
}

/** Read one repayment and its current correction-status ETag. */
export async function fetchRepayment(
  accessToken: string,
  loanId: string,
  repaymentId: string,
): Promise<RepaymentView> {
  const response = await apiClient.get<Envelope<RepaymentResource>>(
    `/loans/${encodeURIComponent(loanId)}/repayments/${encodeURIComponent(repaymentId)}`,
    { accessToken },
  )
  return {
    repayment: unwrapRepayment(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Append a full-reversal correction to a repayment. Corrections are immutable
 * and require a reason; the same idempotency key returns the original result.
 */
export async function correctRepayment(
  accessToken: string,
  loanId: string,
  repaymentId: string,
  reason: string,
  idempotencyKey: string,
): Promise<RepaymentCorrectionView> {
  const response = await apiClient.post<Envelope<RepaymentCorrectionResult>>(
    `/loans/${encodeURIComponent(loanId)}/repayments/${encodeURIComponent(repaymentId)}/corrections`,
    { reason },
    {
      accessToken,
      headers: { 'Idempotency-Key': idempotencyKey },
    },
  )
  const data = response.data?.data
  if (
    !isRecord(data)
    || !isRepaymentResource(data.original_repayment)
    || !isCorrectionResource(data.correction)
    || typeof data.outstanding_principal_kyat !== 'number'
  ) {
    throw invalidResponse(
      response,
      'The server returned an unexpected repayment correction response.',
    )
  }
  return {
    result: data as RepaymentCorrectionResult,
    etag: response.headers.get('ETag'),
  }
}
