import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'

export type CashFlowMonth = {
  month: string
  income_kyat: number
  expense_kyat: number
}

export type CashFlowReport = {
  from: string
  to: string
  months: CashFlowMonth[]
}

export type ExpenseCategoryTotal = {
  category: string
  expense_kyat: number
}

export type ExpenseCategoryReport = {
  from: string
  to: string
  categories: ExpenseCategoryTotal[]
}

type Envelope<T> = { data: T }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isCashFlowMonth(value: unknown): value is CashFlowMonth {
  return isRecord(value)
    && typeof value.month === 'string'
    && typeof value.income_kyat === 'number'
    && Number.isSafeInteger(value.income_kyat)
    && typeof value.expense_kyat === 'number'
    && Number.isSafeInteger(value.expense_kyat)
}

function isExpenseCategory(value: unknown): value is ExpenseCategoryTotal {
  return isRecord(value)
    && typeof value.category === 'string'
    && typeof value.expense_kyat === 'number'
    && Number.isSafeInteger(value.expense_kyat)
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

function reportQuery(fromDate: string, toDate: string): string {
  const query = new URLSearchParams({ from: fromDate, to: toDate })
  return query.toString()
}

export async function fetchCashFlowReport(
  accessToken: string,
  fromDate: string,
  toDate: string,
): Promise<CashFlowReport> {
  const response = await apiClient.get<Envelope<CashFlowReport>>(
    `/reports/cash-flow?${reportQuery(fromDate, toDate)}`,
    { accessToken },
  )
  const data = response.data?.data
  if (
    !isRecord(data)
    || typeof data.from !== 'string'
    || typeof data.to !== 'string'
    || !Array.isArray(data.months)
    || !data.months.every(isCashFlowMonth)
  ) {
    throw invalidResponse(
      response,
      'The server returned an unexpected cash-flow report.',
    )
  }
  return data as CashFlowReport
}

export async function fetchExpenseCategoryReport(
  accessToken: string,
  fromDate: string,
  toDate: string,
): Promise<ExpenseCategoryReport> {
  const response = await apiClient.get<Envelope<ExpenseCategoryReport>>(
    `/reports/expenses-by-category?${reportQuery(fromDate, toDate)}`,
    { accessToken },
  )
  const data = response.data?.data
  if (
    !isRecord(data)
    || typeof data.from !== 'string'
    || typeof data.to !== 'string'
    || !Array.isArray(data.categories)
    || !data.categories.every(isExpenseCategory)
  ) {
    throw invalidResponse(
      response,
      'The server returned an unexpected expense-category report.',
    )
  }
  return data as ExpenseCategoryReport
}

/**
 * Download the server-generated CSV. The backend applies owner scoping and
 * formula-injection protection and returns a UTF-8 BOM.
 */
export async function fetchDashboardCsv(
  accessToken: string,
): Promise<{ content: string; filename: string }> {
  const response = await apiClient.get<string>('/reports/export.csv', {
    accessToken,
    headers: { Accept: 'text/csv' },
  })
  if (typeof response.data !== 'string' || response.data.length === 0) {
    throw invalidResponse(
      response,
      'The server returned an unexpected CSV export.',
    )
  }
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filenameMatch = disposition.match(/filename="([^"]+)"/i)
  return {
    content: response.data,
    filename: filenameMatch?.[1] ?? 'finpath-dashboard-report.csv',
  }
}
