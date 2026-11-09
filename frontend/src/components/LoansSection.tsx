import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useAuth } from '../auth/AuthContext'
import { createIdempotencyKey } from '../auth/api'
import { isApiError } from '../api/errors'
import {
  createLoan,
  createRepayment,
  fetchLoan,
  fetchLoans,
  fetchRepayments,
  updateLoan,
  type LoanResource,
  type RepaymentResource,
} from '../api/loans'
import { formatDateOnly, isValidDateOnly, localDateString } from '../data/dateOnly'
import {
  formatMoney,
  MAX_FINANCIAL_ENTRY_KYAT,
  moneyToEntryString,
  parseMoneyToKyat,
} from '../data/money'

type LoanFormValues = {
  name: string
  principal: string
  startDate: string
}

type RepaymentFormValues = {
  amount: string
  date: string
  note: string
}

type HistoryState = {
  status: 'loading' | 'loaded' | 'error'
  repayments: RepaymentResource[]
  error: string
}

const emptyLoanForm = (): LoanFormValues => ({
  name: '',
  principal: '',
  startDate: localDateString(),
})

const emptyRepaymentForm = (): RepaymentFormValues => ({
  amount: '',
  date: localDateString(),
  note: '',
})

function formatLoanDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function describeError(
  error: unknown,
  fallback: string,
  authMessage = 'Your session has expired. Sign in again to manage loans.',
): string {
  if (!isApiError(error)) return fallback
  if (error.status === 401) return authMessage
  if (error.fieldErrors.length > 0) {
    return error.fieldErrors.map((fieldError) => fieldError.message).join(' ')
  }
  return error.message || fallback
}

function LoansSection() {
  const { user, authorizedRequest } = useAuth()
  const userId = user?.id ?? null

  const [loans, setLoans] = useState<LoanResource[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [loanFormOpen, setLoanFormOpen] = useState(false)
  const [loanForm, setLoanForm] = useState<LoanFormValues>(emptyLoanForm)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loanError, setLoanError] = useState('')
  const [loanNotice, setLoanNotice] = useState('')
  const [loanSubmitting, setLoanSubmitting] = useState(false)

  const [repaymentLoanId, setRepaymentLoanId] = useState<string | null>(null)
  const [repaymentForm, setRepaymentForm] = useState<RepaymentFormValues>(
    emptyRepaymentForm,
  )
  const [repaymentError, setRepaymentError] = useState('')
  const [repaymentSubmitting, setRepaymentSubmitting] = useState(false)

  const [pageError, setPageError] = useState('')
  const [pageNotice, setPageNotice] = useState('')
  const [busyLoanId, setBusyLoanId] = useState<string | null>(null)
  const [history, setHistory] = useState<Record<string, HistoryState>>({})

  const mountedRef = useRef(true)
  const listRequestIdRef = useRef(0)
  const loanSubmittingRef = useRef(false)
  const repaymentSubmittingRef = useRef(false)
  const busyLoanRef = useRef<string | null>(null)
  const editEtagRef = useRef<string | null>(null)
  const createKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)
  const repaymentKeyRef = useRef<{ fingerprint: string; key: string } | null>(
    null,
  )
  const historyRequestIdsRef = useRef<Record<string, number>>({})
  const historyRef = useRef<Record<string, HistoryState>>({})

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadLoans = useCallback(async () => {
    const requestId = ++listRequestIdRef.current
    setListLoading(true)
    setListError('')

    try {
      const result = await authorizedRequest((accessToken) =>
        fetchLoans(accessToken),
      )
      if (!mountedRef.current || requestId !== listRequestIdRef.current) return
      setLoans(result)
    } catch (error) {
      if (!mountedRef.current || requestId !== listRequestIdRef.current) return
      setLoans([])
      setListError(
        describeError(
          error,
          'Unable to load loans. Please try again.',
          'Your session has expired. Sign in again to view loans.',
        ),
      )
    } finally {
      if (mountedRef.current && requestId === listRequestIdRef.current) {
        setListLoading(false)
      }
    }
  }, [authorizedRequest])

  useEffect(() => {
    if (!userId) {
      setListLoading(false)
      setListError('Your session is not available. Sign in again.')
      return
    }
    void loadLoans()
  }, [userId, loadLoans])

  const loadHistory = useCallback(async (loanId: string) => {
    const requestId = (historyRequestIdsRef.current[loanId] ?? 0) + 1
    historyRequestIdsRef.current[loanId] = requestId
    const loadingState: HistoryState = {
      status: 'loading',
      repayments: historyRef.current[loanId]?.repayments ?? [],
      error: '',
    }
    historyRef.current = { ...historyRef.current, [loanId]: loadingState }
    setHistory(historyRef.current)

    try {
      const repayments = await authorizedRequest((accessToken) =>
        fetchRepayments(accessToken, loanId),
      )
      if (
        !mountedRef.current
        || historyRequestIdsRef.current[loanId] !== requestId
      ) {
        return
      }
      historyRef.current = {
        ...historyRef.current,
        [loanId]: {
          status: 'loaded',
          repayments,
          error: '',
        },
      }
      setHistory(historyRef.current)
    } catch (error) {
      if (
        !mountedRef.current
        || historyRequestIdsRef.current[loanId] !== requestId
      ) {
        return
      }
      historyRef.current = {
        ...historyRef.current,
        [loanId]: {
          status: 'error',
          repayments: [],
          error: describeError(
            error,
            'Unable to load repayment history. Please try again.',
            'Your session has expired. Sign in again to view repayments.',
          ),
        },
      }
      setHistory(historyRef.current)
    }
  }, [authorizedRequest])

  function closeLoanForm() {
    setLoanForm(emptyLoanForm())
    setEditingId(null)
    setLoanError('')
    setLoanNotice('')
    setLoanFormOpen(false)
    editEtagRef.current = null
    createKeyRef.current = null
  }

  function applyLoanToForm(loan: LoanResource) {
    setLoanForm({
      name: loan.name,
      principal: moneyToEntryString(loan.principal_kyat),
      startDate: loan.start_date,
    })
  }

  function beginAdd() {
    if (loanSubmittingRef.current || repaymentSubmittingRef.current) return
    setLoanForm(emptyLoanForm())
    setEditingId(null)
    setLoanError('')
    setLoanNotice('')
    setPageError('')
    setPageNotice('')
    setRepaymentLoanId(null)
    setRepaymentError('')
    editEtagRef.current = null
    createKeyRef.current = null
    setLoanFormOpen(true)
  }

  async function beginEdit(loan: LoanResource) {
    if (
      loanSubmittingRef.current
      || repaymentSubmittingRef.current
      || busyLoanRef.current !== null
    ) {
      return
    }

    busyLoanRef.current = loan.id
    setBusyLoanId(loan.id)
    setLoanError('')
    setLoanNotice('')
    setPageError('')
    setPageNotice('')
    setRepaymentLoanId(null)
    setRepaymentError('')

    try {
      const view = await authorizedRequest((accessToken) =>
        fetchLoan(accessToken, loan.id),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setPageError(
          'Unable to edit this loan because the server did not provide a revision. Reload the page and try again.',
        )
        return
      }

      editEtagRef.current = view.etag
      applyLoanToForm(view.loan)
      setLoans((current) => current.map((item) =>
        item.id === view.loan.id ? view.loan : item,
      ))
      setEditingId(view.loan.id)
      setLoanFormOpen(true)
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setPageError('This loan no longer exists. The list is being refreshed.')
        void loadLoans()
        return
      }
      setPageError(
        describeError(
          error,
          'Unable to open this loan for editing. Please try again.',
          'Your session has expired. Sign in again to edit loans.',
        ),
      )
    } finally {
      busyLoanRef.current = null
      if (mountedRef.current) setBusyLoanId(null)
    }
  }

  async function reloadLatestLoanForEdit(loanId: string) {
    try {
      const view = await authorizedRequest((accessToken) =>
        fetchLoan(accessToken, loanId),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setLoanError(
          'The latest revision was not provided. Close the form and reopen the loan.',
        )
        return
      }
      editEtagRef.current = view.etag
      applyLoanToForm(view.loan)
      setEditingId(view.loan.id)
      setLoans((current) => current.map((item) =>
        item.id === view.loan.id ? view.loan : item,
      ))
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setLoanError('This loan no longer exists.')
        setLoanFormOpen(false)
        setEditingId(null)
        void loadLoans()
        return
      }
      setLoanError(
        describeError(
          error,
          'Unable to reload the latest loan. Close the form and try again.',
          'Your session has expired. Sign in again to edit loans.',
        ),
      )
    }
  }

  async function saveLoan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loanSubmittingRef.current) return

    setLoanError('')
    setLoanNotice('')
    setPageError('')
    setPageNotice('')

    const name = loanForm.name.trim()
    const principalKyat = parseMoneyToKyat(loanForm.principal, {
      maxAmount: MAX_FINANCIAL_ENTRY_KYAT,
    })

    if (!name) {
      setLoanError('Enter a loan name.')
      return
    }
    if (name.length > 70) {
      setLoanError('Loan name must be 70 characters or fewer.')
      return
    }
    if (principalKyat === null) {
      setLoanError(
        `Enter a positive whole-Kyat principal up to ${formatMoney(MAX_FINANCIAL_ENTRY_KYAT)}.`,
      )
      return
    }
    if (!isValidDateOnly(loanForm.startDate)) {
      setLoanError('Choose a valid loan start date.')
      return
    }

    const currentLoan = editingId
      ? loans.find((loan) => loan.id === editingId)
      : null
    if (
      editingId
      && currentLoan
      && principalKyat < currentLoan.total_repaid_kyat
    ) {
      setLoanError(
        'Principal cannot be less than repayments already recorded.',
      )
      return
    }

    const input = {
      name,
      principal_kyat: principalKyat,
      start_date: loanForm.startDate,
    }

    loanSubmittingRef.current = true
    setLoanSubmitting(true)

    try {
      if (editingId) {
        const etag = editEtagRef.current
        if (!etag) {
          setLoanError(
            'The current loan revision is missing. Close the form and reopen the loan.',
          )
          return
        }

        await authorizedRequest((accessToken) =>
          updateLoan(accessToken, editingId, input, etag),
        )
        if (!mountedRef.current) return

        const updatedLoanId = editingId
        closeLoanForm()
        setPageNotice('Loan updated.')
        void loadLoans()
        void loadHistory(updatedLoanId)
        return
      }

      const fingerprint = JSON.stringify(input)
      if (
        !createKeyRef.current
        || createKeyRef.current.fingerprint !== fingerprint
      ) {
        createKeyRef.current = {
          fingerprint,
          key: createIdempotencyKey(),
        }
      }

      await authorizedRequest((accessToken) =>
        createLoan(
          accessToken,
          input,
          createKeyRef.current?.key ?? createIdempotencyKey(),
        ),
      )
      if (!mountedRef.current) return

      createKeyRef.current = null
      closeLoanForm()
      setPageNotice('Loan added.')
      void loadLoans()
    } catch (error) {
      if (!mountedRef.current) return

      if (
        editingId
        && isApiError(error)
        && (error.status === 412 || error.status === 428)
      ) {
        setLoanNotice(
          'This loan was changed elsewhere. The latest record has been reloaded; review it and save again.',
        )
        await reloadLatestLoanForEdit(editingId)
        return
      }

      setLoanError(
        describeError(
          error,
          editingId
            ? 'Unable to save this loan. Please try again.'
            : 'Unable to add this loan. Please try again.',
          'Your session has expired. Sign in again to save loans.',
        ),
      )
    } finally {
      loanSubmittingRef.current = false
      if (mountedRef.current) setLoanSubmitting(false)
    }
  }

  function openRepaymentForm(loan: LoanResource) {
    if (
      loanSubmittingRef.current
      || repaymentSubmittingRef.current
      || busyLoanRef.current !== null
    ) {
      return
    }
    setRepaymentLoanId(loan.id)
    setRepaymentForm(emptyRepaymentForm())
    setRepaymentError('')
    setPageError('')
    setPageNotice('')
    setLoanFormOpen(false)
    setLoanError('')
    setLoanNotice('')
    repaymentKeyRef.current = null
  }

  function closeRepaymentForm() {
    setRepaymentLoanId(null)
    setRepaymentForm(emptyRepaymentForm())
    setRepaymentError('')
    repaymentKeyRef.current = null
  }

  async function saveRepayment(
    event: FormEvent<HTMLFormElement>,
    loan: LoanResource,
  ) {
    event.preventDefault()
    if (repaymentSubmittingRef.current) return

    setRepaymentError('')
    setPageError('')
    setPageNotice('')

    const amountKyat = parseMoneyToKyat(repaymentForm.amount, {
      maxAmount: MAX_FINANCIAL_ENTRY_KYAT,
    })
    const note = repaymentForm.note.trim()

    if (amountKyat === null) {
      setRepaymentError(
        `Enter a positive whole-Kyat repayment up to ${formatMoney(MAX_FINANCIAL_ENTRY_KYAT)}.`,
      )
      return
    }
    if (!isValidDateOnly(repaymentForm.date)) {
      setRepaymentError('Choose a valid repayment date.')
      return
    }
    if (note.length > 80) {
      setRepaymentError('Repayment note must be 80 characters or fewer.')
      return
    }
    if (amountKyat > loan.outstanding_principal_kyat) {
      setRepaymentError(
        `Repayment cannot exceed the outstanding principal of ${formatMoney(loan.outstanding_principal_kyat)}.`,
      )
      return
    }

    const input = {
      amount_kyat: amountKyat,
      repayment_date: repaymentForm.date,
      note: note || null,
    }

    repaymentSubmittingRef.current = true
    setRepaymentSubmitting(true)

    try {
      const fingerprint = JSON.stringify({ loanId: loan.id, ...input })
      if (
        !repaymentKeyRef.current
        || repaymentKeyRef.current.fingerprint !== fingerprint
      ) {
        repaymentKeyRef.current = {
          fingerprint,
          key: createIdempotencyKey(),
        }
      }

      await authorizedRequest((accessToken) =>
        createRepayment(
          accessToken,
          loan.id,
          input,
          repaymentKeyRef.current?.key ?? createIdempotencyKey(),
        ),
      )
      if (!mountedRef.current) return

      repaymentKeyRef.current = null
      closeRepaymentForm()
      setPageNotice('Repayment recorded.')
      void loadLoans()
      void loadHistory(loan.id)
    } catch (error) {
      if (!mountedRef.current) return
      setRepaymentError(
        describeError(
          error,
          'Unable to record this repayment. Please try again.',
          'Your session has expired. Sign in again to record repayments.',
        ),
      )
    } finally {
      repaymentSubmittingRef.current = false
      if (mountedRef.current) setRepaymentSubmitting(false)
    }
  }

  const busy = loanSubmitting || repaymentSubmitting || busyLoanId !== null

  return (
    <section className="panel loan-management" aria-labelledby="loan-management-title">
      <div className="management-heading">
        <div>
          <p className="panel-overline">LIVE RECORDS · BACKEND</p>
          <h2 id="loan-management-title">Loans</h2>
          <p className="management-description">
            Track principal and repayments. Outstanding principal is calculated by FinPath; no interest is calculated.
          </p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={beginAdd}
          disabled={busy}
        >
          <span aria-hidden="true">+</span> Add loan
        </button>
      </div>

      {loanFormOpen && (
        <form className="loan-form" noValidate onSubmit={saveLoan} aria-labelledby="loan-form-title">
          <div className="form-heading">
            <h3 id="loan-form-title">{editingId ? 'Edit loan details' : 'Add loan details'}</h3>
            <span>{editingId ? 'Live record' : 'New record'}</span>
          </div>
          {loanError && <p className="form-error" role="alert">{loanError}</p>}
          {loanNotice && <p className="form-notice" role="status">{loanNotice}</p>}
          <div className="loan-form-fields">
            <label>
              <span>Loan name <b aria-hidden="true">*</b></span>
              <input
                autoFocus
                required
                maxLength={70}
                disabled={loanSubmitting}
                value={loanForm.name}
                onChange={(event) => setLoanForm({
                  ...loanForm,
                  name: event.target.value,
                })}
                placeholder="e.g. Car loan"
              />
            </label>
            <label>
              <span>Original principal (MMK) <b aria-hidden="true">*</b></span>
              <input
                required
                type="number"
                min="1"
                max={MAX_FINANCIAL_ENTRY_KYAT}
                step="1"
                inputMode="numeric"
                disabled={loanSubmitting}
                value={loanForm.principal}
                onChange={(event) => setLoanForm({
                  ...loanForm,
                  principal: event.target.value,
                })}
                placeholder="e.g. 180000000"
              />
            </label>
            <label>
              <span>Start date <b aria-hidden="true">*</b></span>
              <input
                required
                type="date"
                disabled={loanSubmitting}
                value={loanForm.startDate}
                onChange={(event) => setLoanForm({
                  ...loanForm,
                  startDate: event.target.value,
                })}
              />
            </label>
          </div>
          <div className="form-actions">
            <button
              className="text-button"
              type="button"
              onClick={closeLoanForm}
              disabled={loanSubmitting}
            >
              Cancel
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={loanSubmitting}
            >
              {loanSubmitting
                ? 'Saving…'
                : editingId ? 'Save loan' : 'Add loan'}
            </button>
          </div>
        </form>
      )}

      {pageError && <p className="form-error" role="alert">{pageError}</p>}
      {pageNotice && <p className="form-notice" role="status">{pageNotice}</p>}

      {listLoading ? (
        <div className="empty-transactions" role="status">
          <strong>Loading loans…</strong>
          <span>Fetching your loan records from FinPath.</span>
        </div>
      ) : listError ? (
        <div className="empty-transactions" role="alert">
          <strong>Unable to load loans</strong>
          <span>{listError}</span>
          <button
            className="text-button"
            type="button"
            onClick={() => { void loadLoans() }}
          >
            Retry
          </button>
        </div>
      ) : loans.length === 0 ? (
        <div className="empty-transactions" role="status">
          <strong>No loans added</strong>
          <span>Add a loan to track principal and repayments here.</span>
        </div>
      ) : (
        <div className="loan-record-list">
          {loans.map((loan) => {
            const outstandingKyat = loan.outstanding_principal_kyat
            const isPaidOff = outstandingKyat === 0
            const loanHistory = history[loan.id]
            return (
              <article className="loan-record" key={loan.id} aria-labelledby={`loan-${loan.id}-title`}>
                <div className="loan-record-heading">
                  <div className="loan-record-title">
                    <span className="loan-icon" aria-hidden="true">{loan.name.charAt(0).toUpperCase()}</span>
                    <div>
                      <h3 id={`loan-${loan.id}-title`}>{loan.name}</h3>
                      <p>
                        Started {formatLoanDate(loan.start_date)}{' '}
                        <span aria-hidden="true">·</span> Live loan
                      </p>
                    </div>
                  </div>
                  <div className="loan-record-actions">
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => { void beginEdit(loan) }}
                      disabled={busy}
                    >
                      {busyLoanId === loan.id ? 'Opening…' : 'Edit details'}
                    </button>
                    {!isPaidOff && (
                      <button
                        className="primary-button"
                        type="button"
                        onClick={() => openRepaymentForm(loan)}
                        disabled={busy}
                      >
                        Record repayment
                      </button>
                    )}
                  </div>
                </div>

                <div className="loan-numbers" aria-label={`${loan.name} principal summary`}>
                  <div><span>Original principal</span><strong>{formatMoney(loan.principal_kyat)}</strong></div>
                  <div><span>Repaid</span><strong>{formatMoney(loan.total_repaid_kyat)}</strong></div>
                  <div className="outstanding-number"><span>Outstanding principal</span><strong>{formatMoney(outstandingKyat)}</strong></div>
                </div>

                {isPaidOff && <p className="paid-off-status" role="status">Paid off · no interest included</p>}

                {repaymentLoanId === loan.id && (
                  <form
                    className="repayment-form"
                    noValidate
                    onSubmit={(event) => { void saveRepayment(event, loan) }}
                    aria-labelledby={`repayment-form-${loan.id}`}
                  >
                    <div className="form-heading">
                      <h4 id={`repayment-form-${loan.id}`}>Record repayment</h4>
                      <span>Up to {formatMoney(outstandingKyat)} remaining</span>
                    </div>
                    {repaymentError && <p className="form-error" role="alert">{repaymentError}</p>}
                    <div className="repayment-fields">
                      <label>
                        <span>Amount (MMK) <b aria-hidden="true">*</b></span>
                        <input
                          autoFocus
                          required
                          type="number"
                          min="1"
                          max={Math.min(outstandingKyat, MAX_FINANCIAL_ENTRY_KYAT)}
                          step="1"
                          inputMode="numeric"
                          disabled={repaymentSubmitting}
                          value={repaymentForm.amount}
                          onChange={(event) => setRepaymentForm({
                            ...repaymentForm,
                            amount: event.target.value,
                          })}
                          placeholder="e.g. 500000"
                        />
                      </label>
                      <label>
                        <span>Date <b aria-hidden="true">*</b></span>
                        <input
                          required
                          type="date"
                          disabled={repaymentSubmitting}
                          value={repaymentForm.date}
                          onChange={(event) => setRepaymentForm({
                            ...repaymentForm,
                            date: event.target.value,
                          })}
                        />
                      </label>
                      <label>
                        <span>Note <small>(optional)</small></span>
                        <input
                          maxLength={80}
                          disabled={repaymentSubmitting}
                          value={repaymentForm.note}
                          onChange={(event) => setRepaymentForm({
                            ...repaymentForm,
                            note: event.target.value,
                          })}
                          placeholder="e.g. Monthly payment"
                        />
                      </label>
                    </div>
                    <div className="form-actions">
                      <button
                        className="text-button"
                        type="button"
                        onClick={closeRepaymentForm}
                        disabled={repaymentSubmitting}
                      >
                        Cancel
                      </button>
                      <button
                        className="primary-button"
                        type="submit"
                        disabled={repaymentSubmitting}
                      >
                        {repaymentSubmitting ? 'Saving…' : 'Save repayment'}
                      </button>
                    </div>
                  </form>
                )}

                <details
                  className="repayment-history"
                  onToggle={(event) => {
                    if (event.currentTarget.open) void loadHistory(loan.id)
                  }}
                >
                  <summary>
                    Repayment history{' '}
                    <span>
                      {loanHistory?.status === 'loaded'
                        ? `${loanHistory.repayments.length} ${loanHistory.repayments.length === 1 ? 'entry' : 'entries'}`
                        : loanHistory?.status === 'loading'
                          ? 'Loading…'
                          : 'View'}
                    </span>
                  </summary>
                  {loanHistory?.status === 'loading' && (
                    <p className="history-empty" role="status">Loading repayments…</p>
                  )}
                  {loanHistory?.status === 'error' && (
                    <p className="history-empty" role="alert">
                      {loanHistory.error}{' '}
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => { void loadHistory(loan.id) }}
                      >
                        Retry history
                      </button>
                    </p>
                  )}
                  {loanHistory?.status === 'loaded' && (
                    loanHistory.repayments.length > 0 ? (
                      <ul>
                        {loanHistory.repayments.map((repayment) => (
                          <li key={repayment.id}>
                            <span>
                              <strong>{repayment.note || 'Repayment'}</strong>
                              <small>
                                {formatLoanDate(repayment.repayment_date)}
                                {repayment.corrected ? ' · Reversed by correction' : ''}
                              </small>
                            </span>
                            <strong>{formatMoney(repayment.amount_kyat)}</strong>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="history-empty">No repayments recorded yet.</p>
                  )}
                </details>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

export default LoansSection
