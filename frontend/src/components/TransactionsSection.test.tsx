import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TransactionsSection from './TransactionsSection'
import { ApiError } from '../api/errors'
import type {
  TransactionCategory,
  TransactionPage,
  TransactionResource,
  TransactionView,
} from '../api/transactions'

const mocks = vi.hoisted(() => ({
  authorizedRequest: vi.fn(),
  fetchTransactionCategories: vi.fn(),
  fetchTransactions: vi.fn(),
  fetchTransaction: vi.fn(),
  createTransaction: vi.fn(),
  updateTransaction: vi.fn(),
  deleteTransaction: vi.fn(),
  createIdempotencyKey: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'alex@example.com' },
    authorizedRequest: mocks.authorizedRequest,
  }),
}))

vi.mock('../api/transactions', () => ({
  fetchTransactionCategories: mocks.fetchTransactionCategories,
  fetchTransactions: mocks.fetchTransactions,
  fetchTransaction: mocks.fetchTransaction,
  createTransaction: mocks.createTransaction,
  updateTransaction: mocks.updateTransaction,
  deleteTransaction: mocks.deleteTransaction,
}))

vi.mock('../auth/api', () => ({
  createIdempotencyKey: mocks.createIdempotencyKey,
}))

const expenseCategory: TransactionCategory = {
  id: 'category-expense-1',
  name: 'Groceries',
  type: 'expense',
}

const incomeCategory: TransactionCategory = {
  id: 'category-income-1',
  name: 'Salary',
  type: 'income',
}

function transactionFixture(
  overrides: Partial<TransactionResource> = {},
): TransactionResource {
  return {
    id: 'transaction-1',
    description: 'Market run',
    type: 'expense',
    category_id: expenseCategory.id,
    category: expenseCategory.name,
    amount_kyat: 42_000,
    transaction_date: '2026-11-03',
    created_at: '2026-11-03T09:00:00+00:00',
    updated_at: '2026-11-03T09:00:00+00:00',
    ...overrides,
  }
}

function viewFixture(
  transaction = transactionFixture(),
  etag: string | null = '"1"',
): TransactionView {
  return { transaction, etag }
}

function pageFixture(
  data: TransactionResource[] = [],
  nextCursor: string | null = null,
): TransactionPage {
  return {
    data,
    pagination: { limit: 50, next_cursor: nextCursor },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

function transactionForm(): HTMLFormElement {
  return document.querySelector('.transaction-form') as HTMLFormElement
}

async function renderLoaded(
  transactions: TransactionResource[] = [transactionFixture()],
  nextCursor: string | null = null,
) {
  mocks.fetchTransactions.mockResolvedValue(
    pageFixture(transactions, nextCursor),
  )
  render(<TransactionsSection />)
  if (transactions.length > 0) {
    await screen.findByText(transactions[0].description)
  } else {
    await screen.findByText('No matching transactions')
  }
}

async function openCreateForm() {
  fireEvent.click(screen.getByRole('button', { name: /Add transaction/ }))
  await screen.findByRole('heading', { name: 'Add transaction' })
  return transactionForm()
}

function fillCreateForm(
  form: HTMLFormElement,
  values: {
    description?: string
    amount?: string
    date?: string
    type?: 'income' | 'expense'
    categoryId?: string
  } = {},
) {
  fireEvent.change(within(form).getByLabelText(/Description/), {
    target: { value: values.description ?? 'Market run' },
  })
  if (values.type) {
    fireEvent.change(within(form).getByLabelText('Type'), {
      target: { value: values.type },
    })
  }
  if (values.categoryId) {
    fireEvent.change(within(form).getByLabelText(/Category/), {
      target: { value: values.categoryId },
    })
  }
  fireEvent.change(within(form).getByLabelText(/Amount/), {
    target: { value: values.amount ?? '42000' },
  })
  fireEvent.change(within(form).getByLabelText(/Date/), {
    target: { value: values.date ?? '2026-11-03' },
  })
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.authorizedRequest.mockImplementation(
    (request: (accessToken: string) => Promise<unknown>) =>
      request('access-token-1'),
  )
  mocks.fetchTransactionCategories.mockResolvedValue([
    expenseCategory,
    incomeCategory,
  ])
  mocks.fetchTransactions.mockResolvedValue(pageFixture())
  mocks.createIdempotencyKey.mockReturnValue('key-1')
})

describe('TransactionsSection backend integration', () => {
  it('loads categories and backend transactions without mock records', async () => {
    await renderLoaded([
      transactionFixture(),
      transactionFixture({
        id: 'transaction-2',
        description: 'October salary',
        type: 'income',
        category_id: incomeCategory.id,
        category: incomeCategory.name,
        amount_kyat: 1_850_000,
        transaction_date: '2026-11-01',
      }),
    ])

    expect(mocks.fetchTransactionCategories).toHaveBeenCalledWith(
      'access-token-1',
    )
    expect(mocks.fetchTransactions).toHaveBeenCalledWith(
      'access-token-1',
      expect.objectContaining({ limit: 50 }),
    )

    const table = screen.getByRole('table')
    expect(within(table).getByText('Market run')).toBeInTheDocument()
    expect(within(table).getByText('Groceries')).toBeInTheDocument()
    expect(within(table).getByText('42,000 MMK')).toBeInTheDocument()
    expect(within(table).getByText('Nov 3')).toBeInTheDocument()
    expect(within(table).getByText('1,850,000 MMK')).toBeInTheDocument()
    expect(screen.getByText('LIVE RECORDS · BACKEND')).toBeInTheDocument()
    expect(screen.queryByText('Northstar Studio')).not.toBeInTheDocument()
    expect(screen.queryByText(/SAMPLE RECORDS/)).not.toBeInTheDocument()
  })

  it('applies backend filters and preserves them when loading the next page', async () => {
    await renderLoaded([transactionFixture()], 'cursor-2')

    fireEvent.change(screen.getByLabelText('Type'), {
      target: { value: 'income' },
    })
    await waitFor(() => {
      expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
        'access-token-1',
        expect.objectContaining({ type: 'income' }),
      )
    })

    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: incomeCategory.id },
    })
    await waitFor(() => {
      expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
        'access-token-1',
        expect.objectContaining({
          type: 'income',
          category_id: incomeCategory.id,
        }),
      )
    })

    fireEvent.change(screen.getByLabelText('Search description'), {
      target: { value: 'salary' },
    })
    await waitFor(() => {
      expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
        'access-token-1',
        expect.objectContaining({ q: 'salary' }),
      )
    })

    fireEvent.change(screen.getByLabelText('From date'), {
      target: { value: '2026-11-01' },
    })
    await waitFor(() => {
      expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
        'access-token-1',
        expect.objectContaining({ from: '2026-11-01' }),
      )
    })

    fireEvent.change(screen.getByLabelText('To date'), {
      target: { value: '2026-11-30' },
    })
    await waitFor(() => {
      expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
        'access-token-1',
        expect.objectContaining({ to: '2026-11-30' }),
      )
    })

    const nextPage = transactionFixture({
      id: 'transaction-3',
      description: 'Monthly salary',
      type: 'income',
      category_id: incomeCategory.id,
      category: incomeCategory.name,
      amount_kyat: 900_000,
    })
    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([nextPage]))
    fireEvent.click(await screen.findByRole('button', {
      name: 'Load more transactions',
    }))

    await screen.findByText('Monthly salary')
    expect(mocks.fetchTransactions).toHaveBeenLastCalledWith(
      'access-token-1',
      expect.objectContaining({
        type: 'income',
        category_id: incomeCategory.id,
        from: '2026-11-01',
        to: '2026-11-30',
        q: 'salary',
        cursor: 'cursor-2',
        limit: 50,
      }),
    )
    expect(screen.getByText('Market run')).toBeInTheDocument()
  })

  it('creates a transaction with a generated idempotency key and blocks duplicates', async () => {
    await renderLoaded()
    const form = await openCreateForm()
    fillCreateForm(form, { description: 'Market run 2', amount: '12500' })

    const pending = deferred<TransactionView>()
    mocks.createTransaction.mockReturnValue(pending.promise)
    const created = transactionFixture({
      id: 'transaction-new',
      description: 'Market run 2',
      amount_kyat: 12_500,
    })
    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([created]))

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createTransaction).toHaveBeenCalledOnce()
    })
    expect(mocks.createIdempotencyKey).toHaveBeenCalledOnce()
    expect(mocks.createTransaction).toHaveBeenCalledWith(
      'access-token-1',
      {
        description: 'Market run 2',
        type: 'expense',
        category_id: expenseCategory.id,
        amount_kyat: 12_500,
        transaction_date: '2026-11-03',
      },
      'key-1',
    )

    pending.resolve(viewFixture(created, '"1"'))
    await screen.findByText('Transaction added.')
    expect(await screen.findByText('Market run 2')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add transaction' }))
      .not.toBeInTheDocument()
  })

  it('reuses the create key for an identical retry and uses a new key when the payload changes', async () => {
    mocks.createIdempotencyKey
      .mockReturnValueOnce('key-1')
      .mockReturnValueOnce('key-2')
    const networkError = new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API.',
    })
    mocks.createTransaction.mockRejectedValueOnce(networkError)
    mocks.createTransaction.mockRejectedValueOnce(networkError)

    await renderLoaded()
    const form = await openCreateForm()
    fillCreateForm(form, { description: 'Retry market', amount: '42000' })

    fireEvent.submit(form)
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createTransaction).toHaveBeenCalledTimes(2)
    })

    fireEvent.change(within(form).getByLabelText(/Amount/), {
      target: { value: '43000' },
    })
    const created = transactionFixture({
      id: 'transaction-retry',
      description: 'Retry market',
      amount_kyat: 43_000,
    })
    mocks.createTransaction.mockResolvedValueOnce(viewFixture(created))
    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([created]))

    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createTransaction).toHaveBeenCalledTimes(3)
    })

    expect(mocks.createTransaction.mock.calls.map((call) => call[2])).toEqual([
      'key-1',
      'key-1',
      'key-2',
    ])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledTimes(2)
    await screen.findByText('Transaction added.')
  })

  it('fetches the current ETag before editing and sends If-Match', async () => {
    const original = transactionFixture({ description: 'Old market run' })
    await renderLoaded([original])
    mocks.fetchTransaction.mockResolvedValue(viewFixture(original, '"2"'))
    const updated = { ...original, description: 'New market run' }
    mocks.updateTransaction.mockResolvedValue(viewFixture(updated, '"3"'))
    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([updated]))

    fireEvent.click(screen.getByRole('button', {
      name: 'Edit Old market run',
    }))
    const form = await waitFor(() => {
      const heading = screen.getByRole('heading', { name: 'Edit transaction' })
      return heading.closest('form') as HTMLFormElement
    })

    expect(mocks.fetchTransaction).toHaveBeenCalledWith(
      'access-token-1',
      original.id,
    )

    fireEvent.change(within(form).getByLabelText(/Description/), {
      target: { value: 'New market run' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save changes',
    }))

    await waitFor(() => {
      expect(mocks.updateTransaction).toHaveBeenCalledWith(
        'access-token-1',
        original.id,
        {
          description: 'New market run',
          type: 'expense',
          category_id: expenseCategory.id,
          amount_kyat: 42_000,
          transaction_date: '2026-11-03',
        },
        '"2"',
      )
    })
    await screen.findByText('Transaction updated.')
    expect(await screen.findByText('New market run')).toBeInTheDocument()
  })

  it('reloads the latest record instead of overwriting a stale edit', async () => {
    const original = transactionFixture()
    const latest = {
      ...original,
      description: 'Changed elsewhere',
      amount_kyat: 50_000,
    }
    await renderLoaded([original])
    mocks.fetchTransaction
      .mockResolvedValueOnce(viewFixture(original, '"2"'))
      .mockResolvedValueOnce(viewFixture(latest, '"3"'))
    mocks.updateTransaction.mockRejectedValue(new ApiError({
      status: 412,
      code: 'stale_revision',
      message: 'The supplied revision does not match the current transaction.',
    }))

    fireEvent.click(screen.getByRole('button', { name: 'Edit Market run' }))
    const form = await waitFor(() => {
      const heading = screen.getByRole('heading', { name: 'Edit transaction' })
      return heading.closest('form') as HTMLFormElement
    })
    fireEvent.change(within(form).getByLabelText(/Description/), {
      target: { value: 'My local change' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save changes',
    }))

    await screen.findByText(/changed elsewhere/)
    await waitFor(() => {
      expect(mocks.fetchTransaction).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateTransaction).toHaveBeenCalledOnce()
    expect(within(form).getByLabelText(/Description/)).toHaveValue(
      'Changed elsewhere',
    )
    expect(within(form).getByLabelText(/Amount/)).toHaveValue(50_000)
    expect(screen.getByRole('heading', { name: 'Edit transaction' }))
      .toBeInTheDocument()
  })

  it('fetches the current ETag before deleting and removes the row only after success', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const transaction = transactionFixture()
    await renderLoaded([transaction])
    mocks.fetchTransaction.mockResolvedValue(viewFixture(transaction, '"7"'))
    const pending = deferred<void>()
    mocks.deleteTransaction.mockReturnValue(pending.promise)

    fireEvent.click(screen.getByRole('button', {
      name: 'Delete Market run',
    }))

    await waitFor(() => {
      expect(mocks.deleteTransaction).toHaveBeenCalledWith(
        'access-token-1',
        transaction.id,
        '"7"',
        'key-1',
      )
    })
    expect(confirm).toHaveBeenCalledOnce()
    expect(screen.getByText('Market run')).toBeInTheDocument()

    pending.resolve()
    await screen.findByText('Transaction deleted.')
    expect(screen.queryByText('Market run')).not.toBeInTheDocument()
  })

  it('reuses the delete idempotency key when retrying the same operation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const transaction = transactionFixture()
    await renderLoaded([transaction])
    mocks.fetchTransaction.mockResolvedValue(viewFixture(transaction, '"7"'))
    mocks.deleteTransaction
      .mockRejectedValueOnce(new ApiError({
        status: null,
        code: 'network_error',
        message: 'Unable to reach the FinPath API.',
      }))
      .mockResolvedValueOnce(undefined)

    fireEvent.click(screen.getByRole('button', {
      name: 'Delete Market run',
    }))
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.click(screen.getByRole('button', {
      name: 'Delete Market run',
    }))

    await waitFor(() => {
      expect(mocks.deleteTransaction).toHaveBeenCalledTimes(2)
    })
    expect(mocks.deleteTransaction.mock.calls.map((call) => call[3])).toEqual([
      'key-1',
      'key-1',
    ])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledOnce()
    await screen.findByText('Transaction deleted.')
  })

  it('does not delete when confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderLoaded()

    fireEvent.click(screen.getByRole('button', {
      name: 'Delete Market run',
    }))

    expect(mocks.fetchTransaction).not.toHaveBeenCalled()
    expect(mocks.deleteTransaction).not.toHaveBeenCalled()
  })

  it('rejects zero, decimal, and over-limit amounts without rounding', async () => {
    await renderLoaded()
    const form = await openCreateForm()
    fillCreateForm(form, { description: 'Test', amount: '0' })

    fireEvent.submit(form)
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat amount/,
    )
    expect(mocks.createTransaction).not.toHaveBeenCalled()

    const amountInput = within(form).getByLabelText(/Amount/)
    fireEvent.change(amountInput, { target: { value: '42.50' } })
    fireEvent.submit(form)
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat amount/,
    )
    expect(mocks.createTransaction).not.toHaveBeenCalled()

    fireEvent.change(amountInput, { target: { value: '1000000001' } })
    fireEvent.submit(form)
    expect(screen.getByRole('alert')).toHaveTextContent(
      /up to 1,000,000,000 MMK/,
    )
    expect(mocks.createTransaction).not.toHaveBeenCalled()

    const maximum = transactionFixture({
      id: 'transaction-max',
      description: 'Test',
      amount_kyat: 1_000_000_000,
    })
    mocks.createTransaction.mockResolvedValue(viewFixture(maximum))
    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([maximum]))
    fireEvent.change(amountInput, { target: { value: '1000000000' } })
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createTransaction).toHaveBeenCalledOnce()
    })
    expect(mocks.createTransaction.mock.calls[0][1]).toMatchObject({
      amount_kyat: 1_000_000_000,
    })
  })

  it('shows loading, empty categories, and empty results states', async () => {
    const categories = deferred<TransactionCategory[]>()
    const page = deferred<TransactionPage>()
    mocks.fetchTransactionCategories.mockReturnValue(categories.promise)
    mocks.fetchTransactions.mockReturnValue(page.promise)

    render(<TransactionsSection />)

    expect(screen.getByText('Loading categories…')).toBeInTheDocument()
    expect(screen.getByText('Fetching the latest records from FinPath.'))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add transaction/ }))
      .toBeDisabled()

    categories.resolve([])
    page.resolve(pageFixture())

    expect(await screen.findByText('No matching transactions'))
      .toBeInTheDocument()
    expect(screen.getByText(
      /No active transaction categories are available/,
    )).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add transaction/ }))
      .toBeDisabled()
  })

  it('shows a list error and retries the same filters', async () => {
    mocks.fetchTransactions.mockRejectedValueOnce(new ApiError({
      status: 500,
      code: 'server_error',
      message: 'The server could not load transactions.',
    }))

    render(<TransactionsSection />)

    expect(await screen.findByText('Unable to load transactions'))
      .toBeInTheDocument()
    expect(screen.getByText('The server could not load transactions.'))
      .toBeInTheDocument()

    mocks.fetchTransactions.mockResolvedValueOnce(pageFixture([
      transactionFixture(),
    ]))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Market run')).toBeInTheDocument()
  })

  it('shows authentication failure and category retry states', async () => {
    mocks.fetchTransactionCategories.mockRejectedValueOnce(new ApiError({
      status: 401,
      code: 'authentication_required',
      message: 'Authentication is required.',
    }))
    mocks.fetchTransactions.mockRejectedValueOnce(new ApiError({
      status: 401,
      code: 'authentication_required',
      message: 'Authentication is required.',
    }))

    render(<TransactionsSection />)

    expect(await screen.findByText(
      /Your session has expired. Sign in again to load categories/,
    )).toBeInTheDocument()
    expect(await screen.findByText(
      /Your session has expired. Sign in again to view transactions/,
    )).toBeInTheDocument()

    mocks.fetchTransactionCategories.mockResolvedValueOnce([
      expenseCategory,
      incomeCategory,
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Retry categories' }))

    await waitFor(() => {
      expect(mocks.fetchTransactionCategories).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      expect(screen.queryByText(
        /Your session has expired. Sign in again to load categories/,
      )).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Add transaction/ }))
      .not.toBeDisabled()
  })
})
