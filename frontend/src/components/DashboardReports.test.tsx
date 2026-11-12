import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardReports, {
  shouldReplaceSettingsEtag,
} from './DashboardReports'
import { ApiError } from '../api/errors'
import type { DashboardOverview } from '../api/dashboard'
import type { CashFlowReport, ExpenseCategoryReport } from '../api/reports'
import type {
  AccountBalanceResource,
  FinancialSettings,
} from '../api/financialSettings'
import { formatMoney } from '../data/money'

const mocks = vi.hoisted(() => ({
  authorizedRequest: vi.fn(),
  fetchFinancialSettings: vi.fn(),
  updateFinancialSettings: vi.fn(),
  fetchDashboardOverview: vi.fn(),
  fetchCashFlowReport: vi.fn(),
  fetchExpenseCategoryReport: vi.fn(),
  fetchDashboardCsv: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'alex@example.com' },
    authorizedRequest: mocks.authorizedRequest,
  }),
}))

vi.mock('../api/financialSettings', () => ({
  fetchFinancialSettings: mocks.fetchFinancialSettings,
  updateFinancialSettings: mocks.updateFinancialSettings,
}))

vi.mock('../api/dashboard', () => ({
  fetchDashboardOverview: mocks.fetchDashboardOverview,
}))

vi.mock('../api/reports', () => ({
  fetchCashFlowReport: mocks.fetchCashFlowReport,
  fetchExpenseCategoryReport: mocks.fetchExpenseCategoryReport,
  fetchDashboardCsv: mocks.fetchDashboardCsv,
}))

function balanceFixture(
  overrides: Partial<AccountBalanceResource> = {},
): AccountBalanceResource {
  return {
    status: 'configured',
    starting_balance_kyat: 800_000,
    effective_date: '2026-10-31',
    amount_kyat: 825_000,
    as_of_date: '2026-11-03',
    ...overrides,
  }
}

function settingsFixture(
  overrides: Partial<FinancialSettings> = {},
): FinancialSettings {
  return {
    ...balanceFixture(),
    ...overrides,
  }
}

function notConfiguredBalance(): AccountBalanceResource {
  return {
    status: 'not_configured',
    starting_balance_kyat: null,
    effective_date: null,
    amount_kyat: null,
    as_of_date: '2026-11-03',
  }
}

function notConfiguredSettings(): FinancialSettings {
  return notConfiguredBalance()
}

function overviewFixture(
  overrides: Partial<DashboardOverview> = {},
): DashboardOverview {
  return {
    account_balance: balanceFixture(),
    all_time_income_kyat: 2_500_000,
    all_time_expense_kyat: 1_000_000,
    travel_expense_kyat: 0,
    total_expense_kyat: 1_000_000,
    current_month_income_kyat: 400_000,
    current_month_expense_kyat: 150_000,
    outstanding_loan_principal_kyat: 50_000_000,
    active_loan_count: 2,
    loan_repayments_kyat: 5_000_000,
    actual_savings_balance_kyat: 300_000,
    savings_target_kyat: 1_000_000,
    active_savings_goal_count: 1,
    savings_goals: [{
      id: 'goal-1',
      name: 'Emergency fund',
      target_kyat: 1_000_000,
      tone: null,
      status: 'active',
      actual_balance_kyat: 300_000,
      remaining_kyat: 700_000,
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-11-01T00:00:00Z',
    }],
    recent_transactions: [{
      id: 'tx-1',
      description: 'Northstar Studio',
      type: 'income',
      category_id: 'cat-1',
      category: 'Salary',
      amount_kyat: 250_000,
      transaction_date: '2026-11-02',
      created_at: '2026-11-02T00:00:00Z',
      updated_at: '2026-11-02T00:00:00Z',
    }],
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

function cashFlowFixture(
  overrides: Partial<CashFlowReport> = {},
): CashFlowReport {
  return {
    from: '2026-06-01',
    to: '2026-11-03',
    months: [
      { month: '2026-06', income_kyat: 100_000, expense_kyat: 40_000 },
      { month: '2026-07', income_kyat: 0, expense_kyat: 25_000 },
      { month: '2026-08', income_kyat: 50_000, expense_kyat: 0 },
      { month: '2026-09', income_kyat: 0, expense_kyat: 10_000 },
      { month: '2026-10', income_kyat: 0, expense_kyat: 0 },
      { month: '2026-11', income_kyat: 400_000, expense_kyat: 150_000 },
    ],
    ...overrides,
  }
}

function categoryFixture(
  overrides: Partial<ExpenseCategoryReport> = {},
): ExpenseCategoryReport {
  return {
    from: '2026-06-01',
    to: '2026-11-03',
    categories: [
      { category: 'Groceries', expense_kyat: 350_000 },
      { category: 'Transport', expense_kyat: 75_000 },
    ],
    ...overrides,
  }
}

async function renderLoaded() {
  render(<DashboardReports />)
  await screen.findByRole('heading', { name: 'Calculated account balance' })
  await waitFor(() => {
    expect(screen.queryByText('Loading cash-flow report…'))
      .not.toBeInTheDocument()
    expect(screen.queryByText('Loading category report…'))
      .not.toBeInTheDocument()
  })
}

function accountBalanceCard(): HTMLElement {
  return document.querySelector('.account-balance-card') as HTMLElement
}

function summaryArticle(label: string): HTMLElement {
  return screen.getByText(label).closest('article') as HTMLElement
}

function openEditForm(): HTMLFormElement {
  fireEvent.click(screen.getByRole('button', { name: 'Edit starting balance' }))
  return document.querySelector('.account-balance-form') as HTMLFormElement
}

function fillBalanceForm(
  form: HTMLFormElement,
  amountKyat: string,
  effectiveDate: string,
) {
  fireEvent.change(within(form).getByLabelText(/Starting bank balance/), {
    target: { value: amountKyat },
  })
  fireEvent.change(within(form).getByLabelText(/Effective date/), {
    target: { value: effectiveDate },
  })
}

beforeEach(() => {
  mocks.authorizedRequest.mockReset()
  mocks.fetchFinancialSettings.mockReset()
  mocks.updateFinancialSettings.mockReset()
  mocks.fetchDashboardOverview.mockReset()
  mocks.fetchCashFlowReport.mockReset()
  mocks.fetchExpenseCategoryReport.mockReset()
  mocks.fetchDashboardCsv.mockReset()

  mocks.authorizedRequest.mockImplementation(
    (request: (accessToken: string) => Promise<unknown>) =>
      request('access-token-1'),
  )
  mocks.fetchFinancialSettings.mockResolvedValue({
    settings: settingsFixture(),
    etag: '"3"',
  })
  mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture())
  mocks.fetchCashFlowReport.mockResolvedValue(cashFlowFixture())
  mocks.fetchExpenseCategoryReport.mockResolvedValue(categoryFixture())
  mocks.fetchDashboardCsv.mockResolvedValue({
    content: 'Section,Record\r\nSummary,Example\r\n',
    filename: 'finpath-dashboard-report.csv',
  })
  mocks.updateFinancialSettings.mockResolvedValue({
    settings: settingsFixture(),
    etag: '"4"',
  })
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:finpath-report'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: vi.fn(),
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('DashboardReports loading and dashboard overview', () => {
  it('shows a loading state until settings and overview resolve', async () => {
    const settingsPromise = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    const overviewPromise = deferred<DashboardOverview>()
    mocks.fetchFinancialSettings.mockReturnValue(settingsPromise.promise)
    mocks.fetchDashboardOverview.mockReturnValue(overviewPromise.promise)

    render(<DashboardReports />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading your dashboard…')
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledWith('access-token-1')
    expect(mocks.fetchDashboardOverview).toHaveBeenCalledWith('access-token-1')

    await act(async () => {
      settingsPromise.resolve({
        settings: settingsFixture(),
        etag: '"3"',
      })
      overviewPromise.resolve(overviewFixture())
    })

    expect(await screen.findByRole('heading', {
      name: 'Calculated account balance',
    })).toBeInTheDocument()
  })

  it('loads the saved starting balance and all dashboard figures from the API', async () => {
    await renderLoaded()

    expect(mocks.fetchFinancialSettings).toHaveBeenCalledOnce()
    expect(mocks.fetchDashboardOverview).toHaveBeenCalledOnce()

    const card = accountBalanceCard()
    expect(within(card).getByText('825,000 MMK')).toBeInTheDocument()
    expect(within(card).getByText(/Starting balance 800,000 MMK/)).toBeInTheDocument()
    expect(within(card).getByText(/Oct 31, 2026/)).toBeInTheDocument()
    expect(within(card).getByText(/As of Nov 3, 2026/)).toBeInTheDocument()

    expect(within(summaryArticle('All-time recorded income'))
      .getByText('2,500,000 MMK')).toBeInTheDocument()
    expect(within(summaryArticle('Total expenses'))
      .getByText('1,000,000 MMK')).toBeInTheDocument()
    expect(within(summaryArticle('Net transaction flow'))
      .getByText('1,500,000 MMK')).toBeInTheDocument()
    expect(screen.getByText('Nov 2026 income / expenses')).toBeInTheDocument()
    expect(screen.getByText('400,000 MMK / 150,000 MMK')).toBeInTheDocument()

    expect(screen.getByText('Northstar Studio')).toBeInTheDocument()
    expect(screen.getByText('+250,000 MMK')).toBeInTheDocument()
    expect(screen.getByText(/Salary/)).toBeInTheDocument()

    expect(screen.getByText('Outstanding principal')).toBeInTheDocument()
    expect(screen.getByText('50,000,000 MMK')).toBeInTheDocument()
    expect(screen.getByText('5,000,000 MMK')).toBeInTheDocument()
    expect(screen.getByText('2 active')).toBeInTheDocument()

    expect(screen.getByText('Emergency fund')).toBeInTheDocument()
    expect(screen.getByText('Combined goal targets')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
    expect(screen.getByText('1 active')).toBeInTheDocument()
  })

  it.each([
    ['zero', 0],
    ['negative', -25_000],
  ])('displays a configured %s starting balance as a real value', async (
    _label,
    startingBalanceKyat,
  ) => {
    const balance = balanceFixture({
      starting_balance_kyat: startingBalanceKyat,
      amount_kyat: startingBalanceKyat,
    })
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: settingsFixture(balance),
      etag: '"3"',
    })
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      account_balance: balance,
    }))

    await renderLoaded()

    const card = accountBalanceCard()
    expect(within(card).getAllByText(formatMoney(startingBalanceKyat)).length)
      .toBeGreaterThan(0)
    expect(within(card).getByText(
      new RegExp(`Starting balance ${formatMoney(startingBalanceKyat)} as of start of`),
    )).toBeInTheDocument()
  })

  it('shows total expenses including recognized travel', async () => {
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      travel_expense_kyat: 500_000,
      total_expense_kyat: 1_500_000,
    }))
    await renderLoaded()

    const card = summaryArticle('Total expenses')
    expect(within(card).getByText('1,500,000 MMK')).toBeInTheDocument()
    expect(within(card).getByText(/500,000 MMK recognized travel/))
      .toBeInTheDocument()
  })

  it('refreshes figures when the dashboard tab becomes active again', async () => {
    const view = render(<DashboardReports active={false} />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })
    expect(mocks.fetchDashboardOverview).toHaveBeenCalledOnce()

    view.rerender(<DashboardReports active />)

    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })
  })

  it('applies an updated backend balance after transactions or allocations change', async () => {
    mocks.fetchFinancialSettings
      .mockResolvedValueOnce({
        settings: settingsFixture({ amount_kyat: 111_111 }),
        etag: '"3"',
      })
      .mockResolvedValueOnce({
        settings: settingsFixture({ amount_kyat: 222_222 }),
        etag: '"3"',
      })
    mocks.fetchDashboardOverview
      .mockResolvedValueOnce(overviewFixture({
        account_balance: balanceFixture({ amount_kyat: 111_111 }),
      }))
      .mockResolvedValueOnce(overviewFixture({
        account_balance: balanceFixture({ amount_kyat: 222_222 }),
      }))

    const view = render(<DashboardReports active={false} />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })
    await waitFor(() => {
      expect(within(accountBalanceCard()).getByText('111,111 MMK'))
        .toBeInTheDocument()
    })

    view.rerender(<DashboardReports active />)

    await waitFor(() => {
      expect(within(accountBalanceCard()).getByText('222,222 MMK'))
        .toBeInTheDocument()
    })
  })

  it('keeps a not-configured balance distinct from zero', async () => {
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: notConfiguredSettings(),
      etag: '"3"',
    })
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      account_balance: notConfiguredBalance(),
    }))

    await renderLoaded()

    const card = accountBalanceCard()
    expect(within(card).getByText('Not configured')).toBeInTheDocument()
    expect(within(card).getByText(/not treated as zero/)).toBeInTheDocument()
    expect(within(card).queryByText(/0 MMK/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Set up starting balance' }))
      .toBeInTheDocument()
  })

  it('shows an empty recent-transactions state', async () => {
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      recent_transactions: [],
    }))

    await renderLoaded()

    expect(screen.getByText('No transactions recorded.')).toBeInTheDocument()
    expect(screen.getByText('0 recent')).toBeInTheDocument()
  })

  it('renders cash-flow and category reports and enables CSV export', async () => {
    await renderLoaded()

    expect(screen.getByRole('button', { name: 'Export dashboard CSV' }))
      .toBeEnabled()
    expect(mocks.fetchCashFlowReport).toHaveBeenCalledWith(
      'access-token-1',
      expect.any(String),
      expect.any(String),
    )
    expect(mocks.fetchExpenseCategoryReport).toHaveBeenCalledWith(
      'access-token-1',
      expect.any(String),
      expect.any(String),
    )

    const cashFlowPanel = screen.getByRole('heading', {
      name: 'Six-month cash flow',
    }).closest('section') as HTMLElement
    expect(within(cashFlowPanel).getByRole('img', {
      name: 'Monthly income and expense bars for the last six months',
    })).toBeInTheDocument()
    expect(within(cashFlowPanel).getByTitle('Nov 2026 income: 400,000 MMK'))
      .toBeInTheDocument()
    expect(within(cashFlowPanel).getByTitle('Nov 2026 expenses: 150,000 MMK'))
      .toBeInTheDocument()

    const categoryPanel = screen.getByRole('heading', {
      name: 'Spending by category',
    }).closest('section') as HTMLElement
    expect(within(categoryPanel).getByText('Groceries')).toBeInTheDocument()
    expect(within(categoryPanel).getByText('350,000 MMK')).toBeInTheDocument()
    expect(within(categoryPanel).getByText('Transport')).toBeInTheDocument()
    expect(within(categoryPanel).getByText('75,000 MMK')).toBeInTheDocument()
    expect(screen.getByText(/Recognized travel is included in Total expenses/))
      .toBeInTheDocument()
  })

  it('shows empty report states when the period has no activity', async () => {
    mocks.fetchCashFlowReport.mockResolvedValue(cashFlowFixture({
      months: cashFlowFixture().months.map((month) => ({
        ...month,
        income_kyat: 0,
        expense_kyat: 0,
      })),
    }))
    mocks.fetchExpenseCategoryReport.mockResolvedValue(categoryFixture({
      categories: [],
    }))

    await renderLoaded()

    expect(screen.getByText(
      'No transactions recorded in this reporting period.',
    )).toBeInTheDocument()
    expect(screen.getByText(
      'No expense transactions recorded in this reporting period.',
    )).toBeInTheDocument()
  })

  it('shows a report error and retries both report requests', async () => {
    const reportError = new ApiError({
      status: 500,
      code: 'internal_error',
      message: 'The report could not be generated.',
    })
    mocks.fetchCashFlowReport
      .mockRejectedValueOnce(reportError)
      .mockResolvedValueOnce(cashFlowFixture())
    mocks.fetchExpenseCategoryReport
      .mockRejectedValueOnce(reportError)
      .mockResolvedValueOnce(categoryFixture())

    await renderLoaded()

    expect(screen.getAllByText('The report could not be generated.'))
      .toHaveLength(2)

    fireEvent.click(screen.getAllByRole('button', { name: 'Retry reports' })[0])

    await waitFor(() => {
      expect(screen.queryByText('The report could not be generated.'))
        .not.toBeInTheDocument()
    })
    expect(screen.getByText('Groceries')).toBeInTheDocument()
  })

  it('downloads the server-generated CSV and reports export failure', async () => {
    await renderLoaded()

    fireEvent.click(screen.getByRole('button', {
      name: 'Export dashboard CSV',
    }))

    expect(await screen.findByText('Dashboard CSV downloaded.'))
      .toBeInTheDocument()
    expect(mocks.fetchDashboardCsv).toHaveBeenCalledWith('access-token-1')

    mocks.fetchDashboardCsv.mockRejectedValueOnce(new ApiError({
      status: 401,
      code: 'authentication_required',
      message: 'Authentication is required.',
    }))
    fireEvent.click(screen.getByRole('button', {
      name: 'Export dashboard CSV',
    }))

    expect(await screen.findByText(
      /Your session has expired. Sign in again to export your dashboard/,
    )).toBeInTheDocument()
  })
})

describe('DashboardReports starting balance saves', () => {
  it.each([
    ['zero', '0', 0],
    ['negative', '-25000', -25_000],
    ['positive', '1000000', 1_000_000],
  ])('saves a %s whole-Kyat balance with the current ETag and refreshes', async (
    _label,
    input,
    expectedKyat,
  ) => {
    const updatedAmountKyat = Math.abs(expectedKyat) + 111_111
    const updatedSettings = settingsFixture({
      starting_balance_kyat: expectedKyat,
      amount_kyat: updatedAmountKyat,
      effective_date: '2026-11-01',
    })
    mocks.updateFinancialSettings.mockResolvedValue({
      settings: updatedSettings,
      etag: '"4"',
    })

    await renderLoaded()

    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: updatedSettings,
      etag: '"4"',
    })
    mocks.fetchDashboardOverview.mockResolvedValueOnce(overviewFixture({
      account_balance: balanceFixture({
        starting_balance_kyat: expectedKyat,
        amount_kyat: updatedAmountKyat,
        effective_date: '2026-11-01',
      }),
    }))

    const form = openEditForm()
    fillBalanceForm(form, input, '2026-11-01')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
        'access-token-1',
        {
          starting_balance_kyat: expectedKyat,
          effective_date: '2026-11-01',
        },
        '"3"',
      )
    })

    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      expect(within(accountBalanceCard())
        .getAllByText(formatMoney(updatedAmountKyat)).length).toBeGreaterThan(0)
    })
  })

  it('saves a first-time balance using the weak ETag delivered by the edge', async () => {
    const updated = settingsFixture({
      starting_balance_kyat: 800_000,
      effective_date: '2026-11-01',
      amount_kyat: 800_000,
    })
    mocks.fetchFinancialSettings
      .mockResolvedValueOnce({
        settings: notConfiguredSettings(),
        etag: 'W/"1"',
      })
      .mockResolvedValueOnce({ settings: updated, etag: 'W/"2"' })
    mocks.fetchDashboardOverview
      .mockResolvedValueOnce(overviewFixture({
        account_balance: notConfiguredBalance(),
      }))
      .mockResolvedValueOnce(overviewFixture({
        account_balance: balanceFixture({
          starting_balance_kyat: 800_000,
          effective_date: '2026-11-01',
          amount_kyat: 800_000,
        }),
      }))
    mocks.updateFinancialSettings.mockResolvedValue({
      settings: updated,
      etag: 'W/"2"',
    })

    await renderLoaded()

    const form = document.querySelector('.account-balance-form') as HTMLFormElement
    expect(form).toBeInTheDocument()
    fillBalanceForm(form, '800000', '2026-11-01')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Set starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
        'access-token-1',
        {
          starting_balance_kyat: 800_000,
          effective_date: '2026-11-01',
        },
        'W/"1"',
      )
    })
    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      expect(within(accountBalanceCard())
        .getAllByText(formatMoney(800_000)).length).toBeGreaterThan(0)
    })
  })

  it('retries a 401 with the same captured ETag without re-reading settings', async () => {
    await renderLoaded()

    const updated = settingsFixture({
      starting_balance_kyat: 1_234_567,
      effective_date: '2026-11-05',
      amount_kyat: 1_234_567,
    })
    const saved = deferred<{ settings: FinancialSettings; etag: string }>()
    mocks.updateFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 401,
        code: 'authentication_required',
        message: 'Authentication is required.',
      }))
      .mockReturnValueOnce(saved.promise)
    mocks.authorizedRequest.mockImplementation(async (
      request: (accessToken: string) => Promise<unknown>,
    ) => {
      try {
        return await request('access-token-1')
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          return request('access-token-2')
        }
        throw error
      }
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      1,
      'access-token-1',
      {
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
      },
      '"3"',
    )
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-2',
      {
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
      },
      '"3"',
    )
    // The retry reuses the captured revision; it must not resync first.
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(1)

    saved.resolve({ settings: updated, etag: '"4"' })
  })

  it('rejects a decimal starting balance without calling the API', async () => {
    await renderLoaded()
    const form = openEditForm()
    fillBalanceForm(form, '250.50', '2026-11-01')

    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(screen.getByRole('alert'))
      .toHaveTextContent(/whole-Kyat starting balance/)
    expect(mocks.updateFinancialSettings).not.toHaveBeenCalled()
  })

  it('clears both starting-balance fields together', async () => {
    const clearedSettings = notConfiguredSettings()
    mocks.updateFinancialSettings.mockResolvedValue({
      settings: clearedSettings,
      etag: '"5"',
    })

    await renderLoaded()

    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: clearedSettings,
      etag: '"5"',
    })
    mocks.fetchDashboardOverview.mockResolvedValueOnce(overviewFixture({
      account_balance: notConfiguredBalance(),
    }))

    openEditForm()
    fireEvent.click(screen.getByRole('button', {
      name: 'Clear starting balance',
    }))

    expect(window.confirm).toHaveBeenCalled()
    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
        'access-token-1',
        { starting_balance_kyat: null, effective_date: null },
        '"3"',
      )
    })

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    await waitFor(() => {
      expect(within(accountBalanceCard()).queryByText(/0 MMK/))
        .not.toBeInTheDocument()
    })
  })

  it('does not clear the balance when the confirmation is declined', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    await renderLoaded()
    openEditForm()

    fireEvent.click(screen.getByRole('button', {
      name: 'Clear starting balance',
    }))

    expect(mocks.updateFinancialSettings).not.toHaveBeenCalled()
  })

  it('reloads settings once to obtain a missing ETag before saving', async () => {
    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: settingsFixture(),
      etag: null,
    })
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: settingsFixture(),
      etag: '"9"',
    })

    await renderLoaded()
    const form = openEditForm()
    fireEvent.change(within(form).getByLabelText(/Starting bank balance/), {
      target: { value: '750000' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
        'access-token-1',
        {
          starting_balance_kyat: 750_000,
          effective_date: '2026-10-31',
        },
        '"9"',
      )
    })
    // One load to read the missing ETag, plus the post-save refresh.
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(3)
  })

  it('shows an error when the ETag is still missing after reloading', async () => {
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: settingsFixture(),
      etag: null,
    })

    await renderLoaded()
    const form = openEditForm()
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByRole('alert'))
      .toHaveTextContent(/did not provide a settings revision/)
    expect(mocks.updateFinancialSettings).not.toHaveBeenCalled()
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
  })

  it.each([
    [412, 'stale_revision', /changed elsewhere/],
    [428, 'precondition_required', /revision was missing/],
  ])('handles a %i conflict by reloading the latest settings', async (
    status,
    code,
    notice,
  ) => {
    mocks.updateFinancialSettings.mockRejectedValue(new ApiError({
      status,
      code,
      message: 'The settings revision is not current.',
    }))

    await renderLoaded()
    const form = openEditForm()
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(notice)).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(document.querySelector('.account-balance-form')).toBeInTheDocument()
  })

  it('resyncs after a 412 and retries with the new ETag', async () => {
    await renderLoaded()

    const updated = settingsFixture({
      starting_balance_kyat: 1_234_567,
      effective_date: '2026-11-05',
      amount_kyat: 1_234_567,
    })

    mocks.updateFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 412,
        code: 'stale_revision',
        message: 'The settings revision is not current.',
      }))
      .mockResolvedValueOnce({ settings: updated, etag: '"5"' })
    mocks.fetchFinancialSettings
      .mockResolvedValueOnce({ settings: settingsFixture(), etag: '"4"' })
      .mockResolvedValueOnce({ settings: updated, etag: '"5"' })
    mocks.fetchDashboardOverview.mockResolvedValueOnce(overviewFixture({
      account_balance: balanceFixture({
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
        amount_kyat: 1_234_567,
      }),
    }))

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_234_567)
    expect(within(form).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-05')

    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-1',
      {
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
      },
      '"4"',
    )
  })

  it('keeps Save disabled while the conflict resync is running', async () => {
    await renderLoaded()

    mocks.updateFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: 412,
      code: 'stale_revision',
      message: 'The settings revision is not current.',
    }))
    const resync = deferred<{ settings: FinancialSettings; etag: string }>()
    mocks.fetchFinancialSettings.mockReturnValueOnce(resync.promise)

    const form = openEditForm()
    fillBalanceForm(form, '1250000', '2026-11-04')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    const busyButton = await within(form).findByRole('button', {
      name: 'Saving…',
    })
    expect(busyButton).toBeDisabled()
    expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)

    fireEvent.click(busyButton)
    expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)

    await act(async () => {
      resync.resolve({ settings: settingsFixture(), etag: '"4"' })
    })

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Save starting balance' }))
      .not.toBeDisabled()
    expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
  })

  it('keeps the typed amount and date after a conflict', async () => {
    await renderLoaded()

    mocks.updateFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: 412,
      code: 'stale_revision',
      message: 'The settings revision is not current.',
    }))
    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: settingsFixture(),
      etag: '"4"',
    })

    const form = openEditForm()
    fillBalanceForm(form, '-75000', '2026-11-07')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(-75_000)
    expect(within(form).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-07')
    expect(form).toBeInTheDocument()
    expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
  })

  it('reconciles a lost PUT response against the saved server values', async () => {
    await renderLoaded()

    const reconciled = settingsFixture({
      starting_balance_kyat: 1_500_000,
      effective_date: '2026-11-06',
      amount_kyat: 1_500_000,
    })

    mocks.updateFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API. Check your connection and try again.',
    }))
    mocks.fetchFinancialSettings
      .mockResolvedValueOnce({ settings: reconciled, etag: '"4"' })
      .mockResolvedValueOnce({ settings: reconciled, etag: '"4"' })
    mocks.fetchDashboardOverview.mockResolvedValueOnce(overviewFixture({
      account_balance: balanceFixture({
        starting_balance_kyat: 1_500_000,
        effective_date: '2026-11-06',
        amount_kyat: 1_500_000,
      }),
    }))

    const form = openEditForm()
    fillBalanceForm(form, '1500000', '2026-11-06')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(/was saved/)).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(within(accountBalanceCard())
      .getAllByText(formatMoney(1_500_000)).length).toBeGreaterThan(0)
  })

  it('refreshes the settings ETag even when the dashboard overview fails', async () => {
    const view = render(<DashboardReports active={false} />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: settingsFixture(),
      etag: '"7"',
    })
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))

    view.rerender(<DashboardReports active />)

    expect(await screen.findByRole('alert'))
      .toHaveTextContent('The database is temporarily unavailable.')

    const form = openEditForm()
    fillBalanceForm(form, '900000', '2026-11-02')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
        'access-token-1',
        {
          starting_balance_kyat: 900_000,
          effective_date: '2026-11-02',
        },
        '"7"',
      )
    })
  })

  it('shows backend validation messages when a save fails', async () => {
    mocks.updateFinancialSettings.mockRejectedValue(new ApiError({
      status: 422,
      code: 'validation_error',
      message: 'The request contains invalid values.',
      fieldErrors: [{
        field: 'starting_balance_kyat',
        code: 'value_error',
        message: 'Starting balance must be within the supported range.',
      }],
    }))

    await renderLoaded()
    const form = openEditForm()
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Starting balance must be within the supported range.')
    expect(document.querySelector('.account-balance-form')).toBeInTheDocument()
  })

  it('keeps an in-progress edit when a background refresh returns settings', async () => {
    const view = render(<DashboardReports />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: settingsFixture({
        starting_balance_kyat: 2_000_000,
        effective_date: '2026-11-01',
        amount_kyat: 2_100_000,
      }),
      etag: '"8"',
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')

    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)

    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_234_567)
    expect(within(form).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-05')
    expect(form).toBeInTheDocument()
  })
})

describe('DashboardReports settings synchronization', () => {
  it('does not let an older settings response replace a resynced ETag', async () => {
    const view = render(<DashboardReports />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    const olderSettings = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    mocks.fetchFinancialSettings
      .mockReturnValueOnce(olderSettings.promise)
      .mockResolvedValueOnce({ settings: settingsFixture(), etag: '"4"' })
    mocks.updateFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 412,
        code: 'stale_revision',
        message: 'The settings revision is not current.',
      }))
      .mockResolvedValueOnce({ settings: settingsFixture(), etag: '"5"' })

    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()

    // This response belongs to the load that started before the resync. It
    // must not reset the revision used by the next manual save.
    await act(async () => {
      olderSettings.resolve({
        settings: settingsFixture({ starting_balance_kyat: 1, amount_kyat: 1 }),
        etag: '"3"',
      })
    })

    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-1',
      {
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
      },
      '"4"',
    )
  })

  it('does not let an older settings response restore a stale ETag after a save', async () => {
    const view = render(<DashboardReports />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    const saved = settingsFixture({
      starting_balance_kyat: 1_000_000,
      effective_date: '2026-11-01',
      amount_kyat: 1_000_000,
    })
    const olderSettings = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    mocks.fetchFinancialSettings
      .mockReturnValueOnce(olderSettings.promise)
      .mockResolvedValueOnce({ settings: saved, etag: '"4"' })
    mocks.updateFinancialSettings.mockResolvedValueOnce({
      settings: saved,
      etag: '"4"',
    })

    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })

    const form = openEditForm()
    fillBalanceForm(form, '1000000', '2026-11-01')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(3)
    })

    // The older load resolves after the PUT advanced the revision. It must not
    // lower the stored ETag for the next save.
    await act(async () => {
      olderSettings.resolve({
        settings: settingsFixture({ starting_balance_kyat: 2, amount_kyat: 2 }),
        etag: '"3"',
      })
    })

    const nextForm = openEditForm()
    fillBalanceForm(nextForm, '2000000', '2026-11-02')
    fireEvent.click(within(nextForm).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-1',
      {
        starting_balance_kyat: 2_000_000,
        effective_date: '2026-11-02',
      },
      '"4"',
    )
  })

  it('shows a configured balance instead of a stale not-configured overview', async () => {
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      account_balance: notConfiguredBalance(),
    }))

    await renderLoaded()

    const card = accountBalanceCard()
    expect(within(card).queryByText('Not configured')).not.toBeInTheDocument()
    expect(within(card).getAllByText(formatMoney(825_000)).length)
      .toBeGreaterThan(0)
    expect(within(card).getByText(/Starting balance 800,000 MMK/))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit starting balance' }))
      .toBeInTheDocument()
  })

  it('keeps configured settings visible when the overview fails', async () => {
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))

    await renderLoaded()

    expect(screen.getByRole('alert'))
      .toHaveTextContent('The database is temporarily unavailable.')
    const card = accountBalanceCard()
    expect(within(card).queryByText('Not configured')).not.toBeInTheDocument()
    expect(within(card).getAllByText(formatMoney(825_000)).length)
      .toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Edit starting balance' }))
      .toBeInTheDocument()
  })

  it('shows the resynced balance in the card and a reopened Edit form', async () => {
    await renderLoaded()

    mocks.updateFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: 412,
      code: 'stale_revision',
      message: 'The settings revision is not current.',
    }))
    const resynced = settingsFixture({
      starting_balance_kyat: 1_500_000,
      effective_date: '2026-11-06',
      amount_kyat: 1_600_000,
    })
    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: resynced,
      etag: '"5"',
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    // The conflict resync must not touch the user's in-progress entry.
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_234_567)

    // The card and the reopened form must use the resynced settings rather
    // than the older configured overview snapshot.
    const card = accountBalanceCard()
    expect(within(card).getAllByText(formatMoney(1_600_000)).length)
      .toBeGreaterThan(0)
    expect(within(card).getByText(/Starting balance 1,500,000 MMK/))
      .toBeInTheDocument()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    const reopened = openEditForm()
    expect(within(reopened).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_500_000)
    expect(within(reopened).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-06')
  })

  it('keeps the captured save revision when the token refreshes between PUT attempts', async () => {
    const view = render(<DashboardReports />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    const newerSettings = settingsFixture({
      starting_balance_kyat: 2_500_000,
      effective_date: '2026-11-08',
      amount_kyat: 2_600_000,
    })
    const pendingSettings = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    mocks.fetchFinancialSettings
      .mockReturnValueOnce(pendingSettings.promise)
      .mockResolvedValueOnce({ settings: newerSettings, etag: '"5"' })

    // Start a background load whose settings GET remains pending.
    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })

    mocks.updateFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 401,
        code: 'authentication_required',
        message: 'Authentication is required.',
      }))
      .mockRejectedValueOnce(new ApiError({
        status: 412,
        code: 'stale_revision',
        message: 'The settings revision is not current.',
      }))

    let retryRequest: ((token: string) => Promise<unknown>) | null = null
    let releaseRetry!: () => void
    const retryGate = new Promise<void>((resolve) => { releaseRetry = resolve })

    mocks.authorizedRequest.mockImplementationOnce(async (
      request: (token: string) => Promise<unknown>,
    ) => {
      retryRequest = request
      await request('access-token-1').catch(() => undefined)
      await retryGate
      return request('access-token-2')
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    })
    expect(retryRequest).not.toBeNull()

    // The background GET observes a newer revision while the token refreshes.
    await act(async () => {
      pendingSettings.resolve({ settings: newerSettings, etag: '"5"' })
    })
    await waitFor(() => {
      expect(within(accountBalanceCard())
        .getAllByText(formatMoney(2_600_000)).length).toBeGreaterThan(0)
    })

    await act(async () => {
      releaseRetry()
    })

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })

    const expectedUpdate = {
      starting_balance_kyat: 1_234_567,
      effective_date: '2026-11-05',
    }
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      1,
      'access-token-1',
      expectedUpdate,
      '"3"',
    )
    // Adopting the newer revision here would silently overwrite the other
    // writer's update instead of surfacing a conflict.
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-2',
      expectedUpdate,
      '"3"',
    )

    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_234_567)
  })

  it('ignores a late settings response that predates a resync on the latest load', async () => {
    const initialSettings = settingsFixture({
      starting_balance_kyat: 800_000,
      effective_date: '2026-10-31',
      amount_kyat: 825_000,
    })
    mocks.fetchFinancialSettings.mockResolvedValueOnce({
      settings: initialSettings,
      etag: null,
    })

    const view = render(<DashboardReports />)
    await screen.findByRole('heading', { name: 'Calculated account balance' })

    const resynced = settingsFixture({
      starting_balance_kyat: 1_750_000,
      effective_date: '2026-11-04',
      amount_kyat: 1_900_000,
    })
    const lateSettings = settingsFixture({
      starting_balance_kyat: 600_000,
      effective_date: '2026-10-15',
      amount_kyat: 600_000,
    })
    const pendingSettings = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    mocks.fetchFinancialSettings
      .mockReturnValueOnce(pendingSettings.promise)
      .mockResolvedValueOnce({ settings: resynced, etag: '"5"' })
      .mockResolvedValue({ settings: resynced, etag: '"5"' })
    mocks.updateFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: 422,
      code: 'validation_error',
      message: 'The request contains invalid values.',
    }))

    // This background load keeps its settings GET pending and remains the
    // latest request, so the generation guard rather than requestIdRef must
    // reject the response when it finally arrives.
    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)
    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    // The initial load had no ETag, so the save resyncs before its PUT.
    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenCalledWith(
      'access-token-1',
      {
        starting_balance_kyat: 1_234_567,
        effective_date: '2026-11-05',
      },
      '"5"',
    )
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('The request contains invalid values.')

    await act(async () => {
      pendingSettings.resolve({ settings: lateSettings, etag: '"4"' })
    })

    const card = accountBalanceCard()
    expect(within(card).getAllByText(formatMoney(1_900_000)).length)
      .toBeGreaterThan(0)
    expect(within(card).getByText(/Starting balance 1,750,000 MMK/))
      .toBeInTheDocument()
    expect(within(card).queryByText(/Starting balance 600,000 MMK/))
      .not.toBeInTheDocument()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    const reopened = openEditForm()
    expect(within(reopened).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_750_000)
    expect(within(reopened).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-04')

    fireEvent.click(within(reopened).getByRole('button', {
      name: 'Save starting balance',
    }))
    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(mocks.updateFinancialSettings).toHaveBeenNthCalledWith(
      2,
      'access-token-1',
      {
        starting_balance_kyat: 1_750_000,
        effective_date: '2026-11-04',
      },
      '"5"',
    )
  })

  it('keeps the PUT-calculated balance when the post-save overview refresh fails', async () => {
    await renderLoaded()

    const updated = settingsFixture({
      starting_balance_kyat: 1_000_000,
      effective_date: '2026-11-01',
      amount_kyat: 1_111_111,
    })
    mocks.updateFinancialSettings.mockResolvedValue({
      settings: updated,
      etag: '"4"',
    })
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: updated,
      etag: '"4"',
    })
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))

    const form = openEditForm()
    fillBalanceForm(form, '1000000', '2026-11-01')
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })

    expect(screen.getByRole('alert'))
      .toHaveTextContent('The database is temporarily unavailable.')
    expect(within(accountBalanceCard())
      .getAllByText(formatMoney(1_111_111)).length).toBeGreaterThan(0)
    expect(within(accountBalanceCard())
      .getByText(/Starting balance 1,000,000 MMK/)).toBeInTheDocument()
  })

  it('keeps the cleared balance when the post-clear overview refresh fails', async () => {
    await renderLoaded()

    const cleared = notConfiguredSettings()
    mocks.updateFinancialSettings.mockResolvedValue({
      settings: cleared,
      etag: '"5"',
    })
    mocks.fetchFinancialSettings.mockResolvedValue({
      settings: cleared,
      etag: '"5"',
    })
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))

    openEditForm()
    fireEvent.click(screen.getByRole('button', {
      name: 'Clear starting balance',
    }))

    await waitFor(() => {
      expect(mocks.updateFinancialSettings).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(mocks.fetchDashboardOverview).toHaveBeenCalledTimes(2)
    })

    const card = accountBalanceCard()
    expect(within(card).getByText('Not configured')).toBeInTheDocument()
    expect(within(card).queryByText(formatMoney(825_000))).not.toBeInTheDocument()
  })

  it('keeps the configured card visible while retrying a failed overview', async () => {
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))
    await renderLoaded()

    const pendingSettings = deferred<{
      settings: FinancialSettings
      etag: string
    }>()
    mocks.fetchFinancialSettings.mockReturnValueOnce(pendingSettings.promise)

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })
    // The retry is still in flight, but the configured card stays mounted.
    expect(screen.getByRole('heading', {
      name: 'Calculated account balance',
    })).toBeInTheDocument()
    expect(within(accountBalanceCard())
      .getAllByText(formatMoney(825_000)).length).toBeGreaterThan(0)

    await act(async () => {
      pendingSettings.resolve({ settings: settingsFixture(), etag: '"3"' })
    })
  })
})

describe('shouldReplaceSettingsEtag', () => {
  it('accepts the first numeric revision', () => {
    expect(shouldReplaceSettingsEtag(null, '"3"')).toBe(true)
  })

  it('ignores a missing incoming ETag', () => {
    expect(shouldReplaceSettingsEtag(null, null)).toBe(false)
    expect(shouldReplaceSettingsEtag('"3"', null)).toBe(false)
  })

  it('treats weak and strong tags as the same numeric revision', () => {
    expect(shouldReplaceSettingsEtag('"3"', 'W/"4"')).toBe(true)
    expect(shouldReplaceSettingsEtag('W/"3"', '"4"')).toBe(true)
    expect(shouldReplaceSettingsEtag('W/"3"', 'W/"3"')).toBe(true)
    expect(shouldReplaceSettingsEtag('W/"3"', 'W/"4"')).toBe(true)
    expect(shouldReplaceSettingsEtag('W/"4"', 'W/"3"')).toBe(false)
  })

  it('keeps a numeric revision when the incoming ETag is malformed', () => {
    expect(shouldReplaceSettingsEtag('"3"', '"not-a-revision"')).toBe(false)
    expect(shouldReplaceSettingsEtag('"3"', 'W/not-a-revision')).toBe(false)
    expect(shouldReplaceSettingsEtag('"3"', '3')).toBe(false)
  })

  it('accepts equal and newer revisions', () => {
    expect(shouldReplaceSettingsEtag('"3"', '"3"')).toBe(true)
    expect(shouldReplaceSettingsEtag('"3"', '"4"')).toBe(true)
  })

  it('rejects a lower revision', () => {
    expect(shouldReplaceSettingsEtag('"4"', '"3"')).toBe(false)
  })

  it('compares unrecognized ETags only when no numeric revision is known', () => {
    expect(shouldReplaceSettingsEtag('"x"', '"x"')).toBe(false)
    expect(shouldReplaceSettingsEtag('"x"', '"y"')).toBe(true)
  })
})

describe('DashboardReports load failures', () => {
  it('shows an API error with a retry action', async () => {
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 503,
      code: 'database_unavailable',
      message: 'The database is temporarily unavailable.',
    }))

    render(<DashboardReports />)

    expect(await screen.findByRole('alert'))
      .toHaveTextContent('The database is temporarily unavailable.')
    // Financial settings loaded even though the overview failed, so the
    // configured balance stays visible alongside the retry action.
    expect(screen.getByRole('heading', {
      name: 'Calculated account balance',
    })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', {
      name: 'Calculated account balance',
    })).toBeInTheDocument()
  })

  it('shows a network error message', async () => {
    mocks.fetchFinancialSettings.mockRejectedValueOnce(new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API. Check your connection and try again.',
    }))

    render(<DashboardReports />)

    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Unable to reach the FinPath API.')
  })

  it('shows an authentication failure message', async () => {
    mocks.fetchDashboardOverview.mockRejectedValueOnce(new ApiError({
      status: 401,
      code: 'authentication_required',
      message: 'Authentication is required.',
    }))

    render(<DashboardReports />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your session has expired. Sign in again to view your dashboard.',
    )
  })

  it('offers a retry and opens the setup form when only settings fail', async () => {
    mocks.fetchFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 503,
        code: 'database_unavailable',
        message: 'The database is temporarily unavailable.',
      }))
      .mockResolvedValueOnce({
        settings: notConfiguredSettings(),
        etag: '"1"',
      })
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      account_balance: notConfiguredBalance(),
    }))

    render(<DashboardReports />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The database is temporarily unavailable.',
    )
    expect(screen.getByRole('heading', {
      name: 'Calculated account balance',
    })).toBeInTheDocument()
    expect(screen.getByText('Not configured')).toBeInTheDocument()
    expect(screen.queryByRole('heading', {
      name: 'Set up starting balance',
    })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', {
      name: 'Set up starting balance',
    })).toBeInTheDocument()
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('applies the missed settings form when the dashboard tab is revisited', async () => {
    mocks.fetchFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 503,
        code: 'database_unavailable',
        message: 'The database is temporarily unavailable.',
      }))
      .mockResolvedValueOnce({
        settings: notConfiguredSettings(),
        etag: '"1"',
      })
    mocks.fetchDashboardOverview.mockResolvedValue(overviewFixture({
      account_balance: notConfiguredBalance(),
    }))

    const view = render(<DashboardReports />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The database is temporarily unavailable.',
    )
    expect(screen.queryByRole('heading', {
      name: 'Set up starting balance',
    })).not.toBeInTheDocument()

    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)

    expect(await screen.findByRole('heading', {
      name: 'Set up starting balance',
    })).toBeInTheDocument()
    expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
  })

  it('does not overwrite an in-progress edit when settings recover', async () => {
    mocks.fetchFinancialSettings
      .mockRejectedValueOnce(new ApiError({
        status: 503,
        code: 'database_unavailable',
        message: 'The database is temporarily unavailable.',
      }))
      .mockResolvedValueOnce({
        settings: settingsFixture({
          starting_balance_kyat: 2_000_000,
          effective_date: '2026-11-01',
          amount_kyat: 2_000_000,
        }),
        etag: '"8"',
      })

    const view = render(<DashboardReports />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The database is temporarily unavailable.',
    )

    const form = openEditForm()
    fillBalanceForm(form, '1234567', '2026-11-05')

    view.rerender(<DashboardReports active={false} />)
    view.rerender(<DashboardReports active />)

    await waitFor(() => {
      expect(mocks.fetchFinancialSettings).toHaveBeenCalledTimes(2)
    })
    expect(within(form).getByLabelText(/Starting bank balance/))
      .toHaveValue(1_234_567)
    expect(within(form).getByLabelText(/Effective date/))
      .toHaveValue('2026-11-05')
    expect(form).toBeInTheDocument()
  })
})
