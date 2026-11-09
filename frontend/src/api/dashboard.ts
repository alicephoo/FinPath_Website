import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'
import type { AccountBalanceResource } from './financialSettings'

export type DashboardTransaction = {
  id: string
  description: string
  type: 'income' | 'expense'
  category_id: string
  category: string
  amount_kyat: number
  transaction_date: string
  created_at: string
  updated_at: string
}

export type DashboardSavingsGoal = {
  id: string
  name: string
  target_kyat: number
  tone: string | null
  status: 'active' | 'archived'
  actual_balance_kyat: number
  remaining_kyat: number
  created_at: string
  updated_at: string
}

/**
 * The implemented Phase 1 subset of `GET /dashboard/overview`.
 *
 * Cash-flow months and expense categories are served by the separate report
 * endpoints in `api/reports.ts`; CSV export is also a separate endpoint.
 * `all_time_expense_kyat` covers transactions only; `travel_expense_kyat` is
 * the recognized travel expense and `total_expense_kyat` combines both.
 */
export type DashboardOverview = {
  account_balance: AccountBalanceResource
  all_time_income_kyat: number
  all_time_expense_kyat: number
  travel_expense_kyat: number
  total_expense_kyat: number
  current_month_income_kyat: number
  current_month_expense_kyat: number
  outstanding_loan_principal_kyat: number
  active_loan_count: number
  loan_repayments_kyat: number
  actual_savings_balance_kyat: number
  savings_target_kyat: number
  active_savings_goal_count: number
  savings_goals: DashboardSavingsGoal[]
  recent_transactions: DashboardTransaction[]
}

type Envelope<T> = {
  data: T
}

function unwrap<T>(response: ApiResponse<Envelope<T>>): T {
  if (!response.data?.data) {
    throw new ApiError({
      status: response.status,
      code: 'invalid_response',
      message: 'The server returned an unexpected dashboard response.',
      headers: response.headers,
    })
  }
  return response.data.data
}

/**
 * Fetch the authenticated caller's owner-scoped dashboard snapshot. All
 * financial totals are calculated by the backend.
 */
export async function fetchDashboardOverview(
  accessToken: string,
  recentLimit = 5,
): Promise<DashboardOverview> {
  const safeLimit = Number.isInteger(recentLimit) && recentLimit >= 1 && recentLimit <= 50
    ? recentLimit
    : 5
  const response = await apiClient.get<Envelope<DashboardOverview>>(
    `/dashboard/overview?recent_limit=${safeLimit}`,
    { accessToken },
  )
  return unwrap(response)
}
