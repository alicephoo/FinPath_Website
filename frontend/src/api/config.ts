/**
 * The API base URL resolution.
 *
 * Local development talks to the FastAPI server directly. `localhost` is used
 * instead of `127.0.0.1` so the local frontend (`localhost:5173`) and API
 * (`localhost:8000`) share a site for the HttpOnly refresh cookie.
 *
 * Production builds default to the same-origin `/api/v1` path served by the
 * Cloudflare Pages Function proxy. A configured `VITE_API_BASE_URL` always
 * wins, and the production default never falls back to localhost.
 */
export function defaultApiBaseUrl(isProduction: boolean): string {
  return isProduction ? '/api/v1' : 'http://localhost:8000/api/v1'
}

export const DEFAULT_API_BASE_URL = import.meta.env.PROD
  ? '/api/v1'
  : 'http://localhost:8000/api/v1'

export function resolveApiBaseUrl(
  configuredBaseUrl?: string,
  defaultBaseUrl = DEFAULT_API_BASE_URL,
): string {
  const trimmed = configuredBaseUrl?.trim()
  const baseUrl = trimmed || defaultBaseUrl
  return baseUrl.replace(/\/+$/, '')
}

export const API_BASE_URL = resolveApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
)

export function buildApiUrl(path: string, baseUrl = API_BASE_URL): string {
  const normalizedBaseUrl = baseUrl.replace(/\/+$/, '')
  const normalizedPath = path.trim().replace(/^\/+/, '')
  return normalizedPath
    ? `${normalizedBaseUrl}/${normalizedPath}`
    : normalizedBaseUrl
}
