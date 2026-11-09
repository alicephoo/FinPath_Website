import { apiClient, type ApiResponse } from './client'
import { ApiError } from './errors'

export type AccountBalanceStatus = 'configured' | 'not_configured'

/**
 * The shared account-balance resource returned by both the financial-settings
 * and dashboard-overview endpoints.
 *
 * `amount_kyat` is the backend-calculated balance. It is `null` only when the
 * status is `not_configured`; an unconfigured balance is not zero.
 */
export type AccountBalanceResource = {
  status: AccountBalanceStatus
  starting_balance_kyat: number | null
  effective_date: string | null
  amount_kyat: number | null
  as_of_date: string
}

export type FinancialSettings = AccountBalanceResource

export type FinancialSettingsUpdate = {
  starting_balance_kyat: number | null
  effective_date: string | null
}

export type FinancialSettingsResult = {
  settings: FinancialSettings
  etag: string | null
}

type Envelope<T> = {
  data: T
}

function unwrap<T>(response: ApiResponse<Envelope<T>>): T {
  if (!response.data?.data) {
    throw new ApiError({
      status: response.status,
      code: 'invalid_response',
      message: 'The server returned an unexpected financial settings response.',
      headers: response.headers,
    })
  }
  return response.data.data
}

/**
 * Read the authenticated caller's own starting-balance setting.
 *
 * The endpoint is `/users/me/...`; there is no user identifier in the request,
 * so the frontend cannot address another user's settings.
 */
export async function fetchFinancialSettings(
  accessToken: string,
): Promise<FinancialSettingsResult> {
  const response = await apiClient.get<Envelope<FinancialSettings>>(
    '/users/me/financial-settings',
    { accessToken },
  )
  return {
    settings: unwrap(response),
    etag: response.headers.get('ETag'),
  }
}

/**
 * Replace both starting-balance fields together. Passing two `null` values
 * clears the setting. The current ETag is sent as `If-Match` so the backend can
 * reject stale revisions with `412 stale_revision`.
 */
export async function updateFinancialSettings(
  accessToken: string,
  update: FinancialSettingsUpdate,
  etag: string,
): Promise<FinancialSettingsResult> {
  const response = await apiClient.put<Envelope<FinancialSettings>>(
    '/users/me/financial-settings',
    update,
    {
      accessToken,
      headers: { 'If-Match': etag },
    },
  )
  return {
    settings: unwrap(response),
    etag: response.headers.get('ETag'),
  }
}
