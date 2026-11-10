import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthContext'
import { isApiError } from '../api/errors'
import {
  fetchDashboardOverview,
  type DashboardOverview,
} from '../api/dashboard'
import {
  fetchCashFlowReport,
  fetchDashboardCsv,
  fetchExpenseCategoryReport,
  type CashFlowReport,
  type ExpenseCategoryReport,
} from '../api/reports'
import {
  fetchFinancialSettings,
  updateFinancialSettings,
  type FinancialSettings,
  type FinancialSettingsUpdate,
} from '../api/financialSettings'
import { isColdStartFailure } from '../api/retry'
import { formatDateOnly, isValidDateOnly, localDateString } from '../data/dateOnly'
import {
  formatMoney,
  moneyToEntryString,
  parseMoneyToKyat,
  sumMoneyKyat,
} from '../data/money'

type LoadOptions = {
  resetForm?: boolean
  keepFormOpen?: boolean
}

function sixMonthRange(): { from: string; to: string } {
  const today = new Date()
  const from = new Date(today.getFullYear(), today.getMonth() - 5, 1)
  return { from: localDateString(from), to: localDateString(today) }
}

function describeLoadError(error: unknown): string {
  if (!isApiError(error)) {
    return 'Unable to load your dashboard. Please try again.'
  }
  if (error.status === 401) {
    return 'Your session has expired. Sign in again to view your dashboard.'
  }
  if (error.isNetworkError) {
    return error.message
  }
  return error.message || 'Unable to load your dashboard. Please try again.'
}

function displayMoney(amountKyat: number | null): string {
  return amountKyat !== null && Number.isSafeInteger(amountKyat)
    ? formatMoney(amountKyat)
    : 'Unavailable'
}

function netFlowKyat(incomeKyat: number, expenseKyat: number): number | null {
  try {
    return sumMoneyKyat([incomeKyat, -expenseKyat])
  } catch {
    return null
  }
}

function savingsProgressPercent(actualKyat: number, targetKyat: number): number {
  if (targetKyat <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((actualKyat / targetKyat) * 100)))
}

function matchesIntendedSettings(
  settings: FinancialSettings,
  intended: FinancialSettingsUpdate,
): boolean {
  return settings.starting_balance_kyat === intended.starting_balance_kyat
    && settings.effective_date === intended.effective_date
}

function DashboardReports({ active = true }: { active?: boolean }) {
  const { user, authorizedRequest } = useAuth()
  const [overview, setOverview] = useState<DashboardOverview | null>(null)
  const [settings, setSettings] = useState<FinancialSettings | null>(null)
  const [cashFlow, setCashFlow] = useState<CashFlowReport | null>(null)
  const [expenseCategories, setExpenseCategories] = useState<ExpenseCategoryReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [reportsLoading, setReportsLoading] = useState(true)
  const [reportsError, setReportsError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportNotice, setExportNotice] = useState('')
  const [exportError, setExportError] = useState('')
  const [saving, setSaving] = useState(false)
  const [balanceFormOpen, setBalanceFormOpen] = useState(false)
  const [balanceForm, setBalanceForm] = useState({
    amount: '',
    effectiveDate: localDateString(),
  })
  const [balanceError, setBalanceError] = useState('')
  const [balanceNotice, setBalanceNotice] = useState('')
  const settingsEtagRef = useRef<string | null>(null)
  const settingsLoadedRef = useRef(false)
  const savingRef = useRef(false)
  const hasLoadedRef = useRef(false)
  const wasActiveRef = useRef(active)
  const requestIdRef = useRef(0)
  const reportsRequestIdRef = useRef(0)
  const mountedRef = useRef(true)
  const userId = user?.id ?? null
  const reportRange = useMemo(() => sixMonthRange(), [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const applySettingsToForm = useCallback((
    nextSettings: FinancialSettings,
    keepFormOpen = false,
  ) => {
    if (
      nextSettings.status === 'configured'
      && nextSettings.starting_balance_kyat !== null
      && nextSettings.effective_date !== null
    ) {
      setBalanceForm({
        amount: moneyToEntryString(nextSettings.starting_balance_kyat),
        effectiveDate: nextSettings.effective_date,
      })
      if (!keepFormOpen) {
        setBalanceFormOpen(false)
      }
    } else {
      setBalanceForm({ amount: '', effectiveDate: localDateString() })
      setBalanceFormOpen(true)
    }
  }, [])

  const loadDashboard = useCallback(async (options: LoadOptions = {}) => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setLoadError('')

    try {
      const result = await authorizedRequest(async (accessToken) => {
        const [settingsOutcome, overviewOutcome] = await Promise.allSettled([
          fetchFinancialSettings(accessToken),
          fetchDashboardOverview(accessToken),
        ])

        // Let authorizedRequest's single refresh-and-retry handle an expired
        // access token instead of silently treating a 401 as partial data.
        if (
          settingsOutcome.status === 'rejected'
          && isApiError(settingsOutcome.reason)
          && settingsOutcome.reason.status === 401
        ) {
          throw settingsOutcome.reason
        }
        if (
          overviewOutcome.status === 'rejected'
          && isApiError(overviewOutcome.reason)
          && overviewOutcome.reason.status === 401
        ) {
          throw overviewOutcome.reason
        }

        return { settingsOutcome, overviewOutcome }
      })

      if (!mountedRef.current || requestId !== requestIdRef.current) return

      const firstLoad = !hasLoadedRef.current
      const recoveredSettingsForm = !settingsLoadedRef.current
        && hasLoadedRef.current
      let firstError: unknown = null

      // Apply the settings and their ETag as soon as they are available. A
      // failing overview request must not prevent the revision from being
      // refreshed, otherwise a 412 recovery would keep resending a stale ETag.
      if (result.settingsOutcome.status === 'fulfilled') {
        const settingsResult = result.settingsOutcome.value
        setSettings(settingsResult.settings)
        settingsEtagRef.current = settingsResult.etag
        if (
          firstLoad
          || options.resetForm
          || (
            recoveredSettingsForm
            && settingsResult.settings.status === 'not_configured'
          )
        ) {
          applySettingsToForm(
            settingsResult.settings,
            options.keepFormOpen,
          )
        }
        settingsLoadedRef.current = true
      } else {
        firstError = result.settingsOutcome.reason
      }

      if (result.overviewOutcome.status === 'fulfilled') {
        setOverview(result.overviewOutcome.value)
      } else if (firstError === null) {
        firstError = result.overviewOutcome.reason
      }

      if (
        result.settingsOutcome.status === 'fulfilled'
        || result.overviewOutcome.status === 'fulfilled'
      ) {
        hasLoadedRef.current = true
      }

      if (firstError !== null) {
        setLoadError(describeLoadError(firstError))
      }
    } catch (error) {
      if (!mountedRef.current || requestId !== requestIdRef.current) return
      setLoadError(describeLoadError(error))
    } finally {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [applySettingsToForm, authorizedRequest])

  const loadReports = useCallback(async () => {
    const requestId = ++reportsRequestIdRef.current
    setReportsLoading(true)
    setReportsError('')

    try {
      const [cashFlowResult, categoryResult] = await authorizedRequest(
        (accessToken) => Promise.all([
          fetchCashFlowReport(
            accessToken,
            reportRange.from,
            reportRange.to,
          ),
          fetchExpenseCategoryReport(
            accessToken,
            reportRange.from,
            reportRange.to,
          ),
        ]),
      )
      if (!mountedRef.current || requestId !== reportsRequestIdRef.current) {
        return
      }
      setCashFlow(cashFlowResult)
      setExpenseCategories(categoryResult)
    } catch (error) {
      if (!mountedRef.current || requestId !== reportsRequestIdRef.current) {
        return
      }
      setReportsError(describeLoadError(error))
    } finally {
      if (mountedRef.current && requestId === reportsRequestIdRef.current) {
        setReportsLoading(false)
      }
    }
  }, [authorizedRequest, reportRange])

  useEffect(() => {
    if (!userId) {
      setLoading(false)
      setReportsLoading(false)
      setLoadError('Your session is not available. Sign in again.')
      return
    }
    void loadDashboard()
    void loadReports()
  }, [userId, loadDashboard, loadReports])

  useEffect(() => {
    if (active && !wasActiveRef.current && hasLoadedRef.current) {
      // Travel, loan, and transaction changes happen in other tabs while this
      // dashboard stays mounted. Refresh authoritative figures on re-entry.
      void loadDashboard()
      void loadReports()
    }
    wasActiveRef.current = active
  }, [active, loadDashboard, loadReports])

  function openBalanceForm() {
    const savedBalance = settings ?? overview?.account_balance ?? null
    if (
      savedBalance?.status === 'configured'
      && savedBalance.starting_balance_kyat !== null
      && savedBalance.effective_date !== null
    ) {
      setBalanceForm({
        amount: moneyToEntryString(savedBalance.starting_balance_kyat),
        effectiveDate: savedBalance.effective_date,
      })
    } else {
      setBalanceForm({ amount: '', effectiveDate: localDateString() })
    }
    setBalanceError('')
    setBalanceNotice('')
    setBalanceFormOpen(true)
  }

  function closeBalanceForm() {
    setBalanceFormOpen(false)
    setBalanceError('')
    setBalanceNotice('')
  }

  /**
   * Read the current settings and revision without touching the form, the
   * dashboard overview, or any in-progress user input. Used to recover from a
   * conflict or an ambiguous save result.
   */
  async function resyncLatestSettings(): Promise<FinancialSettings | null> {
    try {
      const result = await authorizedRequest((accessToken) => (
        fetchFinancialSettings(accessToken)
      ))
      if (!mountedRef.current) return null
      setSettings(result.settings)
      settingsEtagRef.current = result.etag
      return result.settings
    } catch {
      return null
    }
  }

  async function resolveEtag(): Promise<string | null> {
    if (settingsEtagRef.current) return settingsEtagRef.current
    await resyncLatestSettings()
    return settingsEtagRef.current
  }

  async function handleSaveError(
    error: unknown,
    intended: FinancialSettingsUpdate,
  ): Promise<void> {
    if (!isApiError(error)) {
      setBalanceError('Unable to save your starting balance. Please try again.')
      return
    }

    if (error.status === 412 || error.status === 428) {
      // A conflict means the revision advanced elsewhere. Refresh the ETag but
      // leave the user's entry in place so they can save again immediately.
      const latest = await resyncLatestSettings()
      if (!latest) {
        setBalanceError(
          'Your starting balance was changed elsewhere, but the latest settings could not be reloaded. Check your connection and try again.',
        )
        return
      }
      setBalanceNotice(
        error.status === 412
          ? 'Your starting balance was changed elsewhere. The latest settings and revision have been loaded, and your entry has been kept. You can safely save again.'
          : 'The settings revision was missing. The latest settings and revision have been loaded, and your entry has been kept. You can safely save again.',
      )
      return
    }

    if (isColdStartFailure(error)) {
      // The PUT may have committed even though its response was lost. Read the
      // authoritative settings and reconcile instead of blindly retrying with
      // a revision that may already be stale.
      const latest = await resyncLatestSettings()
      if (!latest) {
        setBalanceError(
          'Unable to confirm whether your starting balance was saved because the server could not be reached. Your entry has been kept; check your connection and try again.',
        )
        return
      }
      if (matchesIntendedSettings(latest, intended)) {
        applySettingsToForm(latest, true)
        setBalanceFormOpen(true)
        setBalanceNotice('Your starting balance was saved. Refreshing the dashboard balance…')
        await loadDashboard({ resetForm: true, keepFormOpen: true })
        return
      }
      setBalanceError(
        'The server could not confirm the save. The latest settings and revision have been loaded and your entry has been kept; review it and save again.',
      )
      return
    }

    if (error.status === 401) {
      setBalanceError(
        'Your session has expired. Sign in again to save your starting balance.',
      )
      return
    }
    if (error.fieldErrors.length > 0) {
      setBalanceError(
        error.fieldErrors.map((fieldError) => fieldError.message).join(' '),
      )
      return
    }
    setBalanceError(
      error.message || 'Unable to save your starting balance. Please try again.',
    )
  }

  async function saveStartingBalance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (savingRef.current) return
    setBalanceError('')
    setBalanceNotice('')

    const amountKyat = parseMoneyToKyat(balanceForm.amount, {
      allowNegative: true,
      allowZero: true,
    })
    if (amountKyat === null) {
      setBalanceError('Enter a whole-Kyat starting balance within the supported range.')
      return
    }
    const effectiveDate = balanceForm.effectiveDate
    if (!isValidDateOnly(effectiveDate)) {
      setBalanceError('Choose a valid effective date.')
      return
    }

    // Mark the submission busy before the first await so a second submit
    // cannot resolve and send the same ETag concurrently.
    savingRef.current = true
    setSaving(true)
    try {
      const etag = await resolveEtag()
      if (!etag) {
        setBalanceError(
          'The server did not provide a settings revision. Reload the dashboard and try again.',
        )
        return
      }

      const result = await authorizedRequest((accessToken) =>
        updateFinancialSettings(
          accessToken,
          {
            starting_balance_kyat: amountKyat,
            effective_date: effectiveDate,
          },
          etag,
        ),
      )
      if (!mountedRef.current) return

      setSettings(result.settings)
      settingsEtagRef.current = result.etag
      applySettingsToForm(result.settings)
      setBalanceNotice('Starting balance saved. Refreshing the dashboard balance…')
      await loadDashboard({ resetForm: true })
    } catch (error) {
      if (!mountedRef.current) return
      await handleSaveError(error, {
        starting_balance_kyat: amountKyat,
        effective_date: effectiveDate,
      })
    } finally {
      savingRef.current = false
      if (mountedRef.current) setSaving(false)
    }
  }

  async function clearStartingBalance() {
    const confirmed = window.confirm(
      'Clear the starting balance and effective date? The account balance will be shown as not configured.',
    )
    if (!confirmed) return
    if (savingRef.current) return

    setBalanceError('')
    setBalanceNotice('')

    savingRef.current = true
    setSaving(true)
    try {
      const etag = await resolveEtag()
      if (!etag) {
        setBalanceError(
          'The server did not provide a settings revision. Reload the dashboard and try again.',
        )
        return
      }

      const result = await authorizedRequest((accessToken) =>
        updateFinancialSettings(
          accessToken,
          { starting_balance_kyat: null, effective_date: null },
          etag,
        ),
      )
      if (!mountedRef.current) return

      setSettings(result.settings)
      settingsEtagRef.current = result.etag
      setBalanceForm({ amount: '', effectiveDate: localDateString() })
      setBalanceFormOpen(true)
      setBalanceNotice('Starting balance cleared. Refreshing the dashboard balance…')
      await loadDashboard({ resetForm: true })
    } catch (error) {
      if (!mountedRef.current) return
      await handleSaveError(error, {
        starting_balance_kyat: null,
        effective_date: null,
      })
    } finally {
      savingRef.current = false
      if (mountedRef.current) setSaving(false)
    }
  }

  const balance = overview?.account_balance ?? null
  const configuredBalance = balance?.status === 'configured' ? balance : null
  const recentTransactions = overview?.recent_transactions ?? []
  const savingsGoals = overview?.savings_goals ?? []
  const cashFlowMonths = cashFlow?.months ?? []
  const categoryTotals = expenseCategories?.categories ?? []
  const cashFlowMax = Math.max(
    1,
    ...cashFlowMonths.flatMap((month) => [month.income_kyat, month.expense_kyat]),
  )
  const categoryMax = Math.max(
    1,
    ...categoryTotals.map((category) => category.expense_kyat),
  )
  const hasCashFlow = cashFlowMonths.some(
    (month) => month.income_kyat > 0 || month.expense_kyat > 0,
  )
  const reportPeriod = cashFlow
    ? `${formatDateOnly(cashFlow.from, { month: 'short', year: 'numeric' })} – ${formatDateOnly(cashFlow.to, { month: 'short', year: 'numeric' })}`
    : 'Last 6 months'
  const monthLabel = balance
    ? formatDateOnly(balance.as_of_date, { month: 'short', year: 'numeric' })
    : ''
  const netFlow = overview
    ? netFlowKyat(overview.all_time_income_kyat, overview.all_time_expense_kyat)
    : null

  async function exportCsv() {
    if (exporting) return
    setExporting(true)
    setExportError('')
    setExportNotice('')
    try {
      const result = await authorizedRequest((accessToken) =>
        fetchDashboardCsv(accessToken),
      )
      const blob = new Blob([result.content], {
        type: 'text/csv;charset=utf-8',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = result.filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      setExportNotice('Dashboard CSV downloaded.')
    } catch (error) {
      setExportError(
        isApiError(error) && error.status === 401
          ? 'Your session has expired. Sign in again to export your dashboard.'
          : 'Unable to export the dashboard CSV. Please try again.',
      )
    } finally {
      if (mountedRef.current) setExporting(false)
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">LIVE ACCOUNT SUMMARY</p>
          <h1>Your overview</h1>
        </div>
        <button
          className="primary-button dashboard-export"
          type="button"
          onClick={() => { void exportCsv() }}
          disabled={exporting || !overview}
        >
          {exporting ? 'Exporting…' : 'Export dashboard CSV'}
        </button>
      </div>

      {exportError && <p className="form-error" role="alert">{exportError}</p>}
      {exportNotice && <p className="form-notice" role="status">{exportNotice}</p>}

      <p className="report-disclaimer">
        Summary figures and reports are calculated by the FinPath API from your
        own records. FinPath does not connect to banks or retrieve balances.
      </p>

      {loadError && <p className="form-error" role="alert">{loadError}</p>}

      {loadError && overview && !loading && (
        <div className="form-actions">
          <button
            className="text-button"
            type="button"
            onClick={() => { void loadDashboard() }}
          >
            Retry
          </button>
        </div>
      )}

      {!overview && loading && (
        <section className="panel report-panel" aria-live="polite">
          <p className="report-empty" role="status">Loading your dashboard…</p>
        </section>
      )}

      {!overview && !loading && loadError && (
        <section className="panel report-panel">
          <div className="form-actions">
            <button
              className="primary-button"
              type="button"
              onClick={() => { void loadDashboard() }}
            >
              Retry
            </button>
          </div>
        </section>
      )}

      {overview && (
        <>
          {loading && <p className="report-note" role="status">Refreshing dashboard…</p>}

          <section className="account-balance-card" aria-labelledby="account-balance-title">
            <div className="account-balance-summary">
              <div>
                <p className="panel-overline">CALCULATED FROM YOUR SAVED STARTING BALANCE</p>
                <h2 id="account-balance-title">Calculated account balance</h2>
                {configuredBalance && configuredBalance.amount_kyat !== null ? (
                  <strong className="account-balance-value">
                    {displayMoney(configuredBalance.amount_kyat)}
                  </strong>
                ) : (
                  <strong className="account-balance-value">Not configured</strong>
                )}
                {configuredBalance
                  && configuredBalance.starting_balance_kyat !== null
                  && configuredBalance.effective_date !== null ? (
                    <p className="account-balance-detail">
                      Starting balance {displayMoney(configuredBalance.starting_balance_kyat)} as of end of{' '}
                      {formatDateOnly(configuredBalance.effective_date, { month: 'short', day: 'numeric', year: 'numeric' })};
                      only later transactions and loan cash flows, plus recognized
                      travel, are applied. As of{' '}
                      {formatDateOnly(configuredBalance.as_of_date, { month: 'short', day: 'numeric', year: 'numeric' })}.
                    </p>
                  ) : (
                    <p className="account-balance-detail">
                      No starting balance is configured. The balance is not treated as zero.
                      Set your current bank balance and the date it was effective to calculate it.
                    </p>
                  )}
              </div>
              {configuredBalance && !balanceFormOpen && (
                <button
                  className="icon-button"
                  type="button"
                  onClick={openBalanceForm}
                >
                  Edit starting balance
                </button>
              )}
            </div>

            {balanceFormOpen && (
              <form
                className="account-balance-form"
                noValidate
                onSubmit={saveStartingBalance}
                aria-labelledby="account-balance-form-title"
              >
                <h3 id="account-balance-form-title">
                  {configuredBalance ? 'Review starting balance' : 'Set up starting balance'}
                </h3>
                {balanceNotice && <p className="report-note" role="status">{balanceNotice}</p>}
                {balanceError && <p className="form-error" role="alert">{balanceError}</p>}
                <div className="account-balance-fields">
                  <label>
                    <span>Starting bank balance (MMK) <b aria-hidden="true">*</b></span>
                    <input
                      required
                      type="number"
                      step="1"
                      inputMode="numeric"
                      value={balanceForm.amount}
                      disabled={saving}
                      onChange={(event) => setBalanceForm({
                        ...balanceForm,
                        amount: event.target.value,
                      })}
                      placeholder="e.g. 5000000"
                    />
                    <small>A negative whole-Kyat amount is allowed for an overdraft; zero is allowed.</small>
                  </label>
                  <label>
                    <span>Effective date <b aria-hidden="true">*</b></span>
                    <input
                      required
                      type="date"
                      value={balanceForm.effectiveDate}
                      disabled={saving}
                      onChange={(event) => setBalanceForm({
                        ...balanceForm,
                        effectiveDate: event.target.value,
                      })}
                    />
                    <small>Balance as of the end of this date.</small>
                  </label>
                </div>
                <div className="form-actions">
                  {configuredBalance && !saving && (
                    <button className="text-button" type="button" onClick={closeBalanceForm}>
                      Cancel
                    </button>
                  )}
                  {configuredBalance && !saving && (
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => { void clearStartingBalance() }}
                    >
                      Clear starting balance
                    </button>
                  )}
                  <button className="primary-button" type="submit" disabled={saving}>
                    {saving
                      ? 'Saving…'
                      : configuredBalance
                        ? 'Save starting balance'
                        : 'Set starting balance'}
                  </button>
                </div>
              </form>
            )}
          </section>

          <section className="report-summary-grid" aria-label="Recorded financial summary">
            <article className="report-summary-card">
              <span>All-time recorded income</span>
              <strong>{displayMoney(overview.all_time_income_kyat)}</strong>
              <small>Transactions only</small>
            </article>
            <article className="report-summary-card">
              <span>Total expenses</span>
              <strong>{displayMoney(overview.total_expense_kyat)}</strong>
              <small>
                Transactions plus {displayMoney(overview.travel_expense_kyat)} recognized travel
              </small>
            </article>
            <article className="report-summary-card">
              <span>Net transaction flow</span>
              <strong>{netFlow === null ? 'Unavailable' : displayMoney(netFlow)}</strong>
              <small>Recorded income less expenses; not a balance</small>
            </article>
            <article className="report-summary-card">
              <span>{monthLabel} income / expenses</span>
              <strong>
                {displayMoney(overview.current_month_income_kyat)} /{' '}
                {displayMoney(overview.current_month_expense_kyat)}
              </strong>
              <small>Transactions recorded this month</small>
            </article>
          </section>

          <div className="dashboard-grid report-dashboard-grid">
            <section className="panel report-panel cashflow-panel" aria-labelledby="report-cashflow-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">REPORT</p>
                  <h2 id="report-cashflow-title">Six-month cash flow</h2>
                </div>
                <span className="chart-period">{reportPeriod}</span>
              </div>
              {reportsLoading ? (
                <p className="report-empty" role="status">Loading cash-flow report…</p>
              ) : reportsError ? (
                <div className="report-empty" role="alert">
                  <p>{reportsError}</p>
                  <button className="text-button" type="button" onClick={() => { void loadReports() }}>
                    Retry reports
                  </button>
                </div>
              ) : !hasCashFlow ? (
                <p className="report-empty" role="status">
                  No transactions recorded in this reporting period.
                </p>
              ) : (
                <>
                  <div
                    className="report-flow-chart"
                    role="img"
                    aria-label="Monthly income and expense bars for the last six months"
                  >
                    {cashFlowMonths.map((month) => (
                      <div className="report-flow-month" key={month.month}>
                        <div className="report-flow-bars">
                          <span
                            className="bar income-bar"
                            style={{
                              height: `${month.income_kyat > 0
                                ? Math.max(6, Math.round((month.income_kyat / cashFlowMax) * 100))
                                : 0}%`,
                            }}
                            title={`${formatDateOnly(`${month.month}-01`, { month: 'short', year: 'numeric' })} income: ${formatMoney(month.income_kyat)}`}
                          />
                          <span
                            className="bar expense-bar"
                            style={{
                              height: `${month.expense_kyat > 0
                                ? Math.max(6, Math.round((month.expense_kyat / cashFlowMax) * 100))
                                : 0}%`,
                            }}
                            title={`${formatDateOnly(`${month.month}-01`, { month: 'short', year: 'numeric' })} expenses: ${formatMoney(month.expense_kyat)}`}
                          />
                        </div>
                        <span className="month-label">
                          {formatDateOnly(`${month.month}-01`, { month: 'short' })}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="chart-legend" aria-hidden="true">
                    <span><i className="legend-income" />Income</span>
                    <span><i className="legend-expense" />Expenses</span>
                  </div>
                </>
              )}
            </section>

            <section className="panel report-panel" aria-labelledby="report-categories-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">REPORT</p>
                  <h2 id="report-categories-title">Spending by category</h2>
                </div>
                <span className="panel-count">{reportPeriod}</span>
              </div>
              {reportsLoading ? (
                <p className="report-empty" role="status">Loading category report…</p>
              ) : reportsError ? (
                <div className="report-empty" role="alert">
                  <p>{reportsError}</p>
                  <button className="text-button" type="button" onClick={() => { void loadReports() }}>
                    Retry reports
                  </button>
                </div>
              ) : categoryTotals.length === 0 ? (
                <p className="report-empty" role="status">
                  No expense transactions recorded in this reporting period.
                </p>
              ) : (
                <div className="report-category-list">
                  {categoryTotals.map((category) => (
                    <div className="report-category-row" key={category.category}>
                      <div>
                        <strong>{category.category}</strong>
                        <span>{formatMoney(category.expense_kyat)}</span>
                      </div>
                      <span className="report-category-track">
                        <i
                          style={{
                            width: `${Math.max(
                              3,
                              Math.round((category.expense_kyat / categoryMax) * 100),
                            )}%`,
                          }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel report-panel" aria-labelledby="report-activity-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">LATEST TRANSACTIONS</p>
                  <h2 id="report-activity-title">Recent transactions</h2>
                </div>
                <span className="panel-count">{recentTransactions.length} recent</span>
              </div>
              {recentTransactions.length > 0 ? (
                <ul className="transaction-list">
                  {recentTransactions.map((transaction) => (
                    <li className="transaction-row" key={transaction.id}>
                      <span
                        className={`transaction-icon ${transaction.type}`}
                        aria-hidden="true"
                      >
                        {transaction.description.charAt(0).toUpperCase()}
                      </span>
                      <span className="transaction-description">
                        <strong>{transaction.description}</strong>
                        <small>
                          {transaction.category}
                          <span aria-hidden="true"> · </span>
                          {formatDateOnly(transaction.transaction_date, { month: 'short', day: 'numeric' })}
                        </small>
                      </span>
                      <strong className={`transaction-amount ${transaction.type}`}>
                        {transaction.type === 'income' ? '+' : '−'}
                        {displayMoney(transaction.amount_kyat)}
                      </strong>
                    </li>
                  ))}
                </ul>
              ) : <p className="report-empty" role="status">No transactions recorded.</p>}
            </section>

            <section className="panel report-panel" aria-labelledby="report-loans-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">LIABILITIES · PRINCIPAL ONLY</p>
                  <h2 id="report-loans-title">Loan summary</h2>
                </div>
                <span className="panel-count">
                  {overview.active_loan_count} active
                </span>
              </div>
              <div className="report-loan-totals">
                <div>
                  <span>Outstanding principal</span>
                  <strong>{displayMoney(overview.outstanding_loan_principal_kyat)}</strong>
                </div>
                <div>
                  <span>Recorded principal repayments</span>
                  <strong>{displayMoney(overview.loan_repayments_kyat)}</strong>
                </div>
              </div>
              <p className="report-note">
                Repayments are shown separately and are not included in
                transaction expenses. Original loan principal is not treated as
                an expense. Per-loan details are not included in the dashboard
                overview.
              </p>
            </section>

            <section className="panel report-panel" aria-labelledby="report-savings-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">ACTUAL SAVINGS ENTRIES</p>
                  <h2 id="report-savings-title">Savings progress</h2>
                </div>
                <span className="panel-count">
                  {overview.active_savings_goal_count} active
                </span>
              </div>
              <div className="report-loan-totals">
                <div>
                  <span>Net recorded savings</span>
                  <strong>{displayMoney(overview.actual_savings_balance_kyat)}</strong>
                </div>
                <div>
                  <span>Combined goal targets</span>
                  <strong>{displayMoney(overview.savings_target_kyat)}</strong>
                </div>
              </div>
              <p className="report-note">
                Only recorded contributions less withdrawals count as saved.
                Planned, missed, and outstanding daily-plan amounts are excluded.
              </p>
              {savingsGoals.length > 0 ? (
                <ul className="report-detail-list">
                  {savingsGoals.map((goal) => {
                    const progress = savingsProgressPercent(
                      goal.actual_balance_kyat,
                      goal.target_kyat,
                    )
                    return (
                      <li key={goal.id}>
                        <span>
                          {goal.name}
                          {goal.status === 'archived' && <small>Archived</small>}
                          <small>
                            {displayMoney(goal.actual_balance_kyat)} saved of{' '}
                            {displayMoney(goal.target_kyat)}
                          </small>
                        </span>
                        <strong>
                          {progress}%
                          <small>{displayMoney(goal.remaining_kyat)} remaining</small>
                        </strong>
                      </li>
                    )
                  })}
                </ul>
              ) : <p className="report-empty" role="status">No savings goals recorded.</p>}
            </section>

            <section className="panel report-panel report-travel-panel" aria-labelledby="report-travel-title">
              <div className="panel-heading">
                <div>
                  <p className="panel-overline">REPORT</p>
                  <h2 id="report-travel-title">Travel budgets</h2>
                </div>
                <span className="panel-count">Travel Budget tab</span>
              </div>
              <p className="report-empty" role="status">
                Recognized travel is included in Total expenses. Per-trip
                budgets and spending history stay in the Travel Budget tab; the
                dashboard overview does not return per-trip rows.
              </p>
            </section>
          </div>

          <footer className="dashboard-footer report-footer">
            <span>FinPath <span aria-hidden="true">·</span> Summary figures from the dashboard API</span>
            <span>Unsupported report panels are disabled · loan repayments and savings reported separately</span>
          </footer>
        </>
      )}
    </>
  )
}

export default DashboardReports
