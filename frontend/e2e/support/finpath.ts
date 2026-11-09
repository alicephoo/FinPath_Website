import { randomUUID } from 'node:crypto'
import {
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from '@playwright/test'

/**
 * Shared helpers for the FinPath browser E2E suite.
 *
 * Test accounts are created at runtime with unique e2e-* emails and a random
 * password. No credentials are hard-coded or read from the user's environment.
 * All writes go through the API pointed at the dedicated test database.
 */
const host = '127.0.0.1'
const backendPort = process.env.E2E_BACKEND_PORT ?? '8000'
const frontendPort = process.env.E2E_FRONTEND_PORT ?? '5173'

export const API_URL = `${
  process.env.E2E_BACKEND_URL ?? `http://${host}:${backendPort}`
}/api/v1`
export const ORIGIN = `http://${host}:${frontendPort}`
export const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? `E2e-${randomUUID()}-Aa9!`

export type Credentials = {
  email: string
  password: string
}

export type Session = {
  accessToken: string
  csrfToken: string
  userId: string
  email: string
  credentials: Credentials
}

export type TransactionType = 'income' | 'expense'

export function uniqueEmail(prefix: string): string {
  return `e2e-${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}@example.test`
}

function idempotencyKey(): string {
  return randomUUID()
}

function authHeaders(
  accessToken: string,
  extra: Record<string, string> = {},
): Record<string, string> {
  return {
    Authorization: `Bearer ${accessToken}`,
    Origin: ORIGIN,
    ...extra,
  }
}

async function requireStatus(
  response: Awaited<ReturnType<APIRequestContext['get']>>,
  status: number,
  label: string,
): Promise<void> {
  if (response.status() !== status) {
    throw new Error(
      `${label} returned ${response.status()} (expected ${status}): `
        + `${await response.text()}`,
    )
  }
}

export function isoDate(offsetDays = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export async function registerUser(
  request: APIRequestContext,
  prefix: string,
): Promise<Credentials> {
  const credentials: Credentials = {
    email: uniqueEmail(prefix),
    password: E2E_PASSWORD,
  }
  const response = await request.post(`${API_URL}/auth/register`, {
    data: credentials,
    headers: {
      Origin: ORIGIN,
      'Idempotency-Key': idempotencyKey(),
    },
  })
  await requireStatus(response, 201, 'register')
  return credentials
}

export async function loginUser(
  request: APIRequestContext,
  credentials: Credentials,
): Promise<Session> {
  const response = await request.post(`${API_URL}/auth/login`, {
    data: credentials,
    headers: { Origin: ORIGIN },
  })
  await requireStatus(response, 200, 'login')
  const payload = (await response.json()).data
  return {
    accessToken: payload.access_token,
    csrfToken: payload.csrf_token,
    userId: payload.user.id,
    email: payload.user.email,
    credentials,
  }
}

export async function createUser(
  request: APIRequestContext,
  prefix: string,
): Promise<Session> {
  const credentials = await registerUser(request, prefix)
  return loginUser(request, credentials)
}

export async function listCategories(
  request: APIRequestContext,
  accessToken: string,
): Promise<Array<{ id: string; name: string; type: TransactionType }>> {
  const response = await request.get(`${API_URL}/transaction-categories`, {
    headers: authHeaders(accessToken),
  })
  await requireStatus(response, 200, 'list categories')
  return (await response.json()).data
}

export async function categoryId(
  request: APIRequestContext,
  accessToken: string,
  type: TransactionType,
  name: string,
): Promise<string> {
  const categories = await listCategories(request, accessToken)
  const category =
    categories.find((item) => item.type === type && item.name === name)
    ?? categories.find((item) => item.type === type)
  if (!category) {
    throw new Error(`No ${type} transaction category is available.`)
  }
  return category.id
}

export async function seedTransaction(
  request: APIRequestContext,
  accessToken: string,
  input: {
    description: string
    type?: TransactionType
    category?: string
    amountKyat: number
    date?: string
  },
): Promise<{ id: string }> {
  const type = input.type ?? 'expense'
  const category = await categoryId(
    request,
    accessToken,
    type,
    input.category ?? (type === 'expense' ? 'Groceries' : 'Salary'),
  )
  const response = await request.post(`${API_URL}/transactions`, {
    data: {
      description: input.description,
      type,
      category_id: category,
      amount_kyat: input.amountKyat,
      transaction_date: input.date ?? isoDate(),
    },
    headers: authHeaders(accessToken, {
      'Idempotency-Key': idempotencyKey(),
    }),
  })
  await requireStatus(response, 201, 'create transaction')
  return (await response.json()).data
}

export async function createGoal(
  request: APIRequestContext,
  accessToken: string,
  input: { name: string; targetKyat: number },
): Promise<{ id: string; name: string; actual_balance_kyat: number }> {
  const response = await request.post(`${API_URL}/savings/goals`, {
    data: { name: input.name, target_kyat: input.targetKyat, tone: 'mint' },
    headers: authHeaders(accessToken, {
      'Idempotency-Key': idempotencyKey(),
    }),
  })
  await requireStatus(response, 201, 'create savings goal')
  return (await response.json()).data
}

export async function createContribution(
  request: APIRequestContext,
  accessToken: string,
  goalId: string,
  input: { amountKyat: number; date?: string; note?: string },
): Promise<{ id: string }> {
  const response = await request.post(
    `${API_URL}/savings/goals/${goalId}/entries/contributions`,
    {
      data: {
        amount_kyat: input.amountKyat,
        entry_date: input.date ?? isoDate(),
        note: input.note ?? null,
      },
      headers: authHeaders(accessToken, {
        'Idempotency-Key': idempotencyKey(),
      }),
    },
  )
  await requireStatus(response, 201, 'create contribution')
  return (await response.json()).data
}

export async function attemptWithdrawal(
  request: APIRequestContext,
  accessToken: string,
  goalId: string,
  amountKyat: number,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await request.post(
    `${API_URL}/savings/goals/${goalId}/entries/withdrawals`,
    {
      data: { amount_kyat: amountKyat, entry_date: isoDate() },
      headers: authHeaders(accessToken, {
        'Idempotency-Key': idempotencyKey(),
      }),
    },
  )
  return { status: response.status(), body: await response.json() }
}

export async function fetchGoal(
  request: APIRequestContext,
  accessToken: string,
  goalId: string,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await request.get(`${API_URL}/savings/goals/${goalId}`, {
    headers: authHeaders(accessToken),
  })
  return { status: response.status(), body: await response.json() }
}

export async function createLoan(
  request: APIRequestContext,
  accessToken: string,
  input: { name: string; principalKyat: number; startDate?: string },
): Promise<{ id: string; name: string }> {
  const response = await request.post(`${API_URL}/loans`, {
    data: {
      name: input.name,
      principal_kyat: input.principalKyat,
      start_date: input.startDate ?? isoDate(-30),
    },
    headers: authHeaders(accessToken, {
      'Idempotency-Key': idempotencyKey(),
    }),
  })
  await requireStatus(response, 201, 'create loan')
  return (await response.json()).data
}

export async function createTrip(
  request: APIRequestContext,
  accessToken: string,
  input: {
    name: string
    destination: string
    startDate?: string
    endDate?: string
  },
): Promise<{ id: string; name: string }> {
  const response = await request.post(`${API_URL}/trips`, {
    data: {
      name: input.name,
      destination: input.destination,
      start_date: input.startDate ?? isoDate(7),
      end_date: input.endDate ?? isoDate(14),
    },
    headers: authHeaders(accessToken, {
      'Idempotency-Key': idempotencyKey(),
    }),
  })
  await requireStatus(response, 201, 'create trip')
  return (await response.json()).data
}

export async function createTripBudget(
  request: APIRequestContext,
  accessToken: string,
  tripId: string,
  input: { category: string; budgetKyat: number },
): Promise<{ id: string; category: string }> {
  const response = await request.post(
    `${API_URL}/trips/${tripId}/budgets`,
    {
      data: {
        category: input.category,
        budget_kyat: input.budgetKyat,
      },
      headers: authHeaders(accessToken, {
        'Idempotency-Key': idempotencyKey(),
      }),
    },
  )
  await requireStatus(response, 201, 'create trip budget')
  return (await response.json()).data
}

export async function loginThroughUi(
  page: Page,
  credentials: Credentials,
): Promise<void> {
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: 'Sign in to FinPath' }),
  ).toBeVisible()
  await page.getByLabel(/Email/).fill(credentials.email)
  await page.getByLabel(/Password/).fill(credentials.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Your overview' }),
  ).toBeVisible()
}

export async function openTab(page: Page, name: string): Promise<void> {
  await page.getByRole('tab', { name, exact: true }).click()
}

/**
 * Open a <details> history panel if it is closed.
 *
 * The panels are uncontrolled, and inserting/removing an entry form remounts
 * them closed, so tests cannot assume a prior click left them open.
 */
export async function openDetails(details: Locator): Promise<void> {
  const isOpen = await details.evaluate(
    (element) => (element as HTMLDetailsElement).open,
  )
  if (!isOpen) {
    await details.locator('summary').click()
  }
}
