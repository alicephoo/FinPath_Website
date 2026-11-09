import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { apiClient } from '../api/client'
import { AuthProvider, useAuth } from './AuthContext'

const fetchMock = vi.fn<typeof fetch>()

type RouteHandler = (init: RequestInit) => Response | Promise<Response>

const routes = new Map<string, RouteHandler>()

const testUser = {
  id: 'user-1',
  email: 'alex@example.com',
  roles: ['user'],
  status: 'active',
  email_verified: true,
  timezone: 'Asia/Yangon',
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return new Response(JSON.stringify(body), { ...init, headers })
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  fieldErrors: Array<{ field: string; code: string; message: string }> = [],
): Response {
  return jsonResponse({
    error: {
      code,
      message,
      field_errors: fieldErrors,
      request_id: 'request-test',
    },
  }, { status })
}

function loginBody(
  accessToken = 'access-token-1',
  csrfToken = 'csrf-token-1',
  user = testUser,
) {
  return {
    data: {
      access_token: accessToken,
      csrf_token: csrfToken,
      token_type: 'Bearer',
      expires_in: 900,
      user,
    },
  }
}

function route(method: string, path: string, handler: RouteHandler) {
  routes.set(`${method.toUpperCase()} ${path}`, handler)
}

function installFetch() {
  fetchMock.mockImplementation(async (input, init = {}) => {
    const url = new URL(String(input))
    const method = (init.method ?? 'GET').toUpperCase()
    const handler = routes.get(`${method} ${url.pathname}`)
    if (!handler) {
      return errorResponse(404, 'not_found', 'Not found.')
    }
    return handler(init)
  })
  vi.stubGlobal('fetch', fetchMock)
}

function callsFor(method: string, path: string) {
  return fetchMock.mock.calls.filter(([input, init]) => {
    const url = new URL(String(input))
    return url.pathname === path
      && (init?.method ?? 'GET').toUpperCase() === method.toUpperCase()
  })
}

function lastCall(method: string, path: string) {
  const calls = callsFor(method, path)
  return calls[calls.length - 1]
}

function headersFor(call: [RequestInfo | URL, RequestInit?]): Headers {
  return new Headers(call[1]?.headers)
}

function mockAnonymousRestore() {
  route('GET', '/api/v1/auth/csrf', () => errorResponse(
    401,
    'authentication_required',
    'Authentication is required.',
  ))
}

function mockAuthenticatedRestore({
  startCsrf = 'csrf-start',
  accessToken = 'access-token-1',
  sessionCsrf = 'csrf-session',
} = {}) {
  route('GET', '/api/v1/auth/csrf', () => jsonResponse({
    data: { csrf_token: startCsrf },
  }))
  route('POST', '/api/v1/auth/refresh', () => jsonResponse(
    loginBody(accessToken, sessionCsrf),
  ))
  route('GET', '/api/v1/auth/me', () => jsonResponse({ data: testUser }))
}

const unconfiguredBalance = {
  status: 'not_configured',
  starting_balance_kyat: null,
  effective_date: null,
  amount_kyat: null,
  as_of_date: '2026-11-03',
}

function mockDashboardData() {
  route('GET', '/api/v1/users/me/financial-settings', () => jsonResponse(
    { data: unconfiguredBalance },
    { headers: { ETag: '"1"' } },
  ))
  route('GET', '/api/v1/transaction-categories', () => jsonResponse({
    data: [],
  }))
  route('GET', '/api/v1/transactions', () => jsonResponse({
    data: [],
    pagination: { limit: 50, next_cursor: null },
  }))
  route('GET', '/api/v1/loans', () => jsonResponse({ data: [] }))
  route('GET', '/api/v1/savings/goals', () => jsonResponse({ data: [] }))
  route('GET', '/api/v1/dashboard/overview', () => jsonResponse({
    data: {
      account_balance: unconfiguredBalance,
      all_time_income_kyat: 0,
      all_time_expense_kyat: 0,
      current_month_income_kyat: 0,
      current_month_expense_kyat: 0,
      outstanding_loan_principal_kyat: 0,
      active_loan_count: 0,
      loan_repayments_kyat: 0,
      actual_savings_balance_kyat: 0,
      savings_target_kyat: 0,
      active_savings_goal_count: 0,
      savings_goals: [],
      recent_transactions: [],
    },
  }))
}

async function renderLogin() {
  render(<App />)
  await screen.findByRole('heading', { name: 'Sign in to FinPath' })
}

beforeEach(() => {
  routes.clear()
  fetchMock.mockReset()
  localStorage.clear()
  sessionStorage.clear()
  mockAnonymousRestore()
  mockDashboardData()
  installFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('registration and email verification', () => {
  it('registers with an idempotency key and does not sign the user in', async () => {
    route('POST', '/api/v1/auth/register', () => jsonResponse({
      data: {
        id: 'user-new',
        email: 'new@example.com',
        roles: ['user'],
        status: 'active',
        verification_status: 'required',
      },
    }, { status: 201 }))

    await renderLogin()
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    await screen.findByRole('heading', { name: 'Create your account' })

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'correct horse battery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await screen.findByRole('heading', { name: 'Verify your email' })
    expect(screen.getByText(/Account created/)).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()

    const call = lastCall('POST', '/api/v1/auth/register')
    expect(call).toBeDefined()
    expect(headersFor(call!).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(String(call![1]?.body))).toEqual({
      email: 'new@example.com',
      password: 'correct horse battery',
    })
  })

  it('returns to login with a success message when verification is not required', async () => {
    route('POST', '/api/v1/auth/register', () => jsonResponse({
      data: {
        id: 'user-new',
        email: 'new@example.com',
        roles: ['user'],
        status: 'active',
        verification_status: 'not_required',
      },
    }, { status: 201 }))

    await renderLogin()
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    await screen.findByRole('heading', { name: 'Create your account' })

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'correct horse battery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(screen.queryByRole('heading', { name: 'Verify your email' }))
      .not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Account created. Sign in with your new credentials.',
    )
    expect(screen.getByLabelText('Email')).toHaveValue('new@example.com')
  })

  it('requests a new verification token and consumes a verification token', async () => {
    route('POST', '/api/v1/auth/email-verification-requests', () => jsonResponse({
      data: { status: 'accepted' },
    }, { status: 202 }))
    route('POST', '/api/v1/auth/email-verification/consume', () => jsonResponse({
      data: { email_verified: true },
    }))

    await renderLogin()
    fireEvent.click(screen.getByRole('button', { name: 'Verify a new account' }))
    await screen.findByRole('heading', { name: 'Verify your email' })

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.click(screen.getByRole('button', {
      name: 'Send new verification token',
    }))
    await screen.findByText(/new verification message has been sent/)

    const resendCall = lastCall(
      'POST',
      '/api/v1/auth/email-verification-requests',
    )
    expect(headersFor(resendCall!).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(String(resendCall![1]?.body))).toEqual({
      email: 'new@example.com',
    })

    fireEvent.change(screen.getByLabelText('Verification token'), {
      target: { value: 'verification-token-1234567890' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }))

    await screen.findByRole('heading', { name: 'Email verified' })
    const consumeCall = lastCall(
      'POST',
      '/api/v1/auth/email-verification/consume',
    )
    expect(headersFor(consumeCall!).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(String(consumeCall![1]?.body))).toEqual({
      token: 'verification-token-1234567890',
    })

    fireEvent.click(screen.getByRole('button', { name: 'Continue to sign in' }))
    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
  })
})

describe('login and protected application access', () => {
  it('logs in with cookie credentials and reveals the protected shell', async () => {
    route('POST', '/api/v1/auth/login', () => jsonResponse(loginBody()))

    await renderLogin()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'alex@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'correct horse battery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('tablist')
    expect(screen.getByText('alex@example.com')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Dashboard' })).toBeInTheDocument()

    const call = lastCall('POST', '/api/v1/auth/login')
    expect(call![1]?.credentials).toBe('include')
    expect(JSON.parse(String(call![1]?.body))).toEqual({
      email: 'alex@example.com',
      password: 'correct horse battery',
    })
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('returns to the login screen when session restoration fails', async () => {
    render(<App />)

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})

describe('session restoration', () => {
  it('restores a session through CSRF, refresh, and the current-user endpoint', async () => {
    mockAuthenticatedRestore()

    render(<App />)

    // Restoration starts in the loading state; the login form must not flash
    // before the refresh cookie has been validated.
    expect(screen.getByText('Restoring your session…')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Sign in to FinPath' }))
      .not.toBeInTheDocument()

    await screen.findByRole('tablist')
    expect(screen.getByText('alex@example.com')).toBeInTheDocument()

    const csrfCall = lastCall('GET', '/api/v1/auth/csrf')
    expect(csrfCall![1]?.credentials).toBe('include')

    const refreshCall = lastCall('POST', '/api/v1/auth/refresh')
    expect(refreshCall![1]?.credentials).toBe('include')
    expect(headersFor(refreshCall!).get('X-CSRF-Token')).toBe('csrf-start')

    const meCall = lastCall('GET', '/api/v1/auth/me')
    expect(headersFor(meCall!).get('Authorization')).toBe(
      'Bearer access-token-1',
    )

    await waitFor(() => {
      expect(callsFor('GET', '/api/v1/users/me/financial-settings'))
        .toHaveLength(1)
      expect(callsFor('GET', '/api/v1/dashboard/overview')).toHaveLength(1)
    })
    expect(await screen.findByText('Not configured')).toBeInTheDocument()
  })

  it('returns to login when the refresh session is invalid', async () => {
    route('GET', '/api/v1/auth/csrf', () => jsonResponse({
      data: { csrf_token: 'csrf-start' },
    }))
    route('POST', '/api/v1/auth/refresh', () => errorResponse(
      401,
      'authentication_required',
      'Authentication is required.',
    ))

    render(<App />)

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
    expect(callsFor('GET', '/api/v1/auth/me')).toHaveLength(0)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('retries only the safe CSRF GET after a backend cold-start failure', async () => {
    let csrfAttempts = 0
    route('GET', '/api/v1/auth/csrf', () => {
      csrfAttempts += 1
      if (csrfAttempts === 1) {
        return errorResponse(
          503,
          'database_unavailable',
          'The database is temporarily unavailable.',
        )
      }
      return jsonResponse({ data: { csrf_token: 'csrf-start' } })
    })
    route('POST', '/api/v1/auth/refresh', () => jsonResponse(loginBody()))
    route('GET', '/api/v1/auth/me', () => jsonResponse({ data: testUser }))

    render(<App />)

    await screen.findByRole('tablist', {}, { timeout: 3000 })
    expect(callsFor('GET', '/api/v1/auth/csrf')).toHaveLength(2)
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
    expect(callsFor('GET', '/api/v1/auth/me')).toHaveLength(1)
  })

  it('offers a safe retry when the CSRF GET stays unavailable', async () => {
    route('GET', '/api/v1/auth/csrf', () => errorResponse(
      503,
      'proxy_upstream_unavailable',
      'The API backend is temporarily unavailable.',
    ))

    render(<App />)

    await screen.findByRole(
      'heading',
      { name: 'Session restore paused' },
      { timeout: 3000 },
    )
    expect(callsFor('GET', '/api/v1/auth/csrf')).toHaveLength(3)
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(0)
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()

    mockAuthenticatedRestore()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await screen.findByRole('tablist', {}, { timeout: 3000 })
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
  })

  it('does not retry a refresh POST after an uncertain cold-start failure', async () => {
    route('GET', '/api/v1/auth/csrf', () => jsonResponse({
      data: { csrf_token: 'csrf-start' },
    }))
    route('POST', '/api/v1/auth/refresh', () => errorResponse(
      503,
      'database_unavailable',
      'The database is temporarily unavailable.',
    ))

    render(<App />)

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
    expect(callsFor('GET', '/api/v1/auth/me')).toHaveLength(0)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'To protect your sign-in, the request was not retried automatically.',
    )
  })

  it('does not retry a refresh POST after an uncertain network failure', async () => {
    route('GET', '/api/v1/auth/csrf', () => jsonResponse({
      data: { csrf_token: 'csrf-start' },
    }))
    route('POST', '/api/v1/auth/refresh', () => {
      throw new TypeError('network failure')
    })

    render(<App />)

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
    expect(callsFor('GET', '/api/v1/auth/me')).toHaveLength(0)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'To protect your sign-in, the request was not retried automatically.',
    )
  })

  it('retries the safe current-user GET after a successful refresh', async () => {
    let meAttempts = 0
    route('GET', '/api/v1/auth/csrf', () => jsonResponse({
      data: { csrf_token: 'csrf-start' },
    }))
    route('POST', '/api/v1/auth/refresh', () => jsonResponse(loginBody()))
    route('GET', '/api/v1/auth/me', () => {
      meAttempts += 1
      if (meAttempts === 1) {
        return errorResponse(
          503,
          'database_unavailable',
          'The database is temporarily unavailable.',
        )
      }
      return jsonResponse({ data: testUser })
    })

    render(<App />)

    await screen.findByRole('tablist', {}, { timeout: 3000 })
    expect(callsFor('POST', '/api/v1/auth/refresh')).toHaveLength(1)
    expect(callsFor('GET', '/api/v1/auth/me')).toHaveLength(2)
  })

  it('refreshes once after a 401 and retries without a refresh loop', async () => {
    let refreshCalls = 0
    route('GET', '/api/v1/auth/csrf', () => jsonResponse({
      data: { csrf_token: 'csrf-start' },
    }))
    route('POST', '/api/v1/auth/refresh', () => {
      refreshCalls += 1
      return jsonResponse(loginBody(
        refreshCalls === 1 ? 'access-token-1' : 'access-token-2',
        refreshCalls === 1 ? 'csrf-token-1' : 'csrf-token-2',
      ))
    })
    route('GET', '/api/v1/auth/me', () => jsonResponse({ data: testUser }))
    route('GET', '/api/v1/probe', (init) => {
      const authorization = new Headers(init.headers).get('Authorization')
      if (authorization === 'Bearer access-token-2') {
        return jsonResponse({ data: { ok: true } })
      }
      return errorResponse(
        401,
        'authentication_required',
        'Authentication is required.',
      )
    })

    function RefreshProbe() {
      const { status, authorizedRequest } = useAuth()
      const [result, setResult] = useState('')

      if (status !== 'authenticated') {
        return <p>status: {status}</p>
      }

      async function runProbe() {
        try {
          const responses = await Promise.all([
            authorizedRequest((accessToken) => apiClient.get<{
              data: { ok: boolean }
            }>('/probe', { accessToken })),
            authorizedRequest((accessToken) => apiClient.get<{
              data: { ok: boolean }
            }>('/probe', { accessToken })),
          ])
          setResult(responses.map((response) => (
            String(response.data?.data.ok)
          )).join(','))
        } catch {
          setResult('error')
        }
      }

      return (
        <>
          <p>probe ready</p>
          <button type="button" onClick={() => { void runProbe() }}>
            Run probe
          </button>
          <p>{result}</p>
        </>
      )
    }

    render(
      <AuthProvider>
        <RefreshProbe />
      </AuthProvider>,
    )

    await screen.findByText('probe ready')
    expect(refreshCalls).toBe(1)

    fireEvent.click(screen.getByRole('button', { name: 'Run probe' }))

    await screen.findByText('true,true')
    expect(refreshCalls).toBe(2)
    const refreshCallsList = callsFor('POST', '/api/v1/auth/refresh')
    expect(headersFor(refreshCallsList[1]).get('X-CSRF-Token')).toBe(
      'csrf-token-1',
    )
    expect(callsFor('GET', '/api/v1/probe')).toHaveLength(4)
    expect(headersFor(lastCall('GET', '/api/v1/probe')!).get('Authorization'))
      .toBe('Bearer access-token-2')
  })
})

describe('logout', () => {
  it('sends the CSRF token, clears local state, and returns to login', async () => {
    mockAuthenticatedRestore({ sessionCsrf: 'csrf-session' })
    route('POST', '/api/v1/auth/logout', () => new Response(null, {
      status: 204,
    }))

    render(<App />)
    await screen.findByRole('tablist')
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }))

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()

    const call = lastCall('POST', '/api/v1/auth/logout')
    expect(call![1]?.credentials).toBe('include')
    expect(headersFor(call!).get('X-CSRF-Token')).toBe('csrf-session')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('clears local state even when the logout request fails', async () => {
    mockAuthenticatedRestore()
    route('POST', '/api/v1/auth/logout', () => errorResponse(
      503,
      'database_unavailable',
      'The database is temporarily unavailable.',
    ))

    render(<App />)
    await screen.findByRole('tablist')
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }))

    await screen.findByRole('heading', { name: 'Sign in to FinPath' })
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })
})

describe('authentication errors', () => {
  it('shows invalid-credential errors without leaving the login screen', async () => {
    route('POST', '/api/v1/auth/login', () => errorResponse(
      401,
      'authentication_failed',
      'The email or password is incorrect.',
    ))

    await renderLogin()
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'alex@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'wrong-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The email or password is incorrect.',
    )
    expect(screen.getByRole('heading', { name: 'Sign in to FinPath' }))
      .toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('moves unverified accounts to the verification screen', async () => {
    route('POST', '/api/v1/auth/login', () => errorResponse(
      403,
      'email_verification_required',
      'Verify your email address before signing in.',
    ))

    await renderLogin()
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'alex@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'correct horse battery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('heading', { name: 'Verify your email' })
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Verify your email address before signing in.',
    )
    expect(screen.getByLabelText('Email')).toHaveValue('alex@example.com')
  })

  it.each([
    ['account_suspended', 'This account cannot access the application. Contact support for assistance.'],
    ['account_closed', 'This account cannot access the application. Contact support for assistance.'],
  ])('shows the backend message for %s', async (code, message) => {
    route('POST', '/api/v1/auth/login', () => errorResponse(403, code, message))

    await renderLogin()
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'alex@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'correct horse battery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('shows backend validation details for registration', async () => {
    route('POST', '/api/v1/auth/register', () => errorResponse(
      422,
      'validation_error',
      'The request contains invalid values.',
      [{
        field: 'password',
        code: 'value_error',
        message: 'Password must be at least 12 characters.',
      }],
    ))

    await renderLogin()
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    await screen.findByRole('heading', { name: 'Create your account' })

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'twelve-chars' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await screen.findByRole('alert')
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'password: Password must be at least 12 characters.',
      )
    })
  })
})
