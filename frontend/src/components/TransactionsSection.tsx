import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useAuth } from '../auth/AuthContext'
import { createIdempotencyKey } from '../auth/api'
import { isApiError } from '../api/errors'
import {
  createTransaction,
  deleteTransaction,
  fetchTransaction,
  fetchTransactionCategories,
  fetchTransactions,
  updateTransaction,
  type TransactionCategory,
  type TransactionCreateInput,
  type TransactionListFilters,
  type TransactionResource,
  type TransactionType,
} from '../api/transactions'
import { formatDateOnly, isValidDateOnly, localDateString } from '../data/dateOnly'
import {
  MAX_FINANCIAL_ENTRY_KYAT,
  formatMoney,
  moneyToEntryString,
  parseMoneyToKyat,
} from '../data/money'

const PAGE_SIZE = 50
const ALL = 'all'

type TypeFilter = typeof ALL | TransactionType

type TransactionFormValues = {
  description: string
  type: TransactionType
  categoryId: string
  amount: string
  date: string
}

type FilterState = {
  search: string
  type: TypeFilter
  categoryId: string
  from: string
  to: string
}

const initialFilters: FilterState = {
  search: '',
  type: ALL,
  categoryId: ALL,
  from: '',
  to: '',
}

function blankForm(
  categories: TransactionCategory[],
  type: TransactionType = 'expense',
): TransactionFormValues {
  return {
    description: '',
    type,
    categoryId: categories.find((category) => category.type === type)?.id ?? '',
    amount: '',
    date: localDateString(),
  }
}

function formatTransactionAmount(amountKyat: number): string {
  return formatMoney(amountKyat)
}

function formatTransactionDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
  })
}

function describeError(
  error: unknown,
  fallback: string,
  authMessage = 'Your session has expired. Sign in again.',
): string {
  if (!isApiError(error)) return fallback
  if (error.status === 401) return authMessage
  if (error.fieldErrors.length > 0) {
    return error.fieldErrors.map((fieldError) => fieldError.message).join(' ')
  }
  return error.message || fallback
}

function TransactionsSection() {
  const { user, authorizedRequest } = useAuth()
  const userId = user?.id ?? null

  const [categories, setCategories] = useState<TransactionCategory[]>([])
  const [categoriesLoading, setCategoriesLoading] = useState(true)
  const [categoriesError, setCategoriesError] = useState('')
  const [transactions, setTransactions] = useState<TransactionResource[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [listLoading, setListLoading] = useState(true)
  const [listLoadingMore, setListLoadingMore] = useState(false)
  const [listError, setListError] = useState('')
  const [filters, setFilters] = useState<FilterState>(initialFilters)
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editCategoryFallback, setEditCategoryFallback] =
    useState<TransactionCategory | null>(null)
  const [formValues, setFormValues] = useState<TransactionFormValues>(() =>
    blankForm([]),
  )
  const [formError, setFormError] = useState('')
  const [formNotice, setFormNotice] = useState('')
  const [formSubmitting, setFormSubmitting] = useState(false)
  const [actionError, setActionError] = useState('')
  const [successNotice, setSuccessNotice] = useState('')
  const [busyRecordId, setBusyRecordId] = useState<string | null>(null)

  const mountedRef = useRef(true)
  const categoryRequestIdRef = useRef(0)
  const listRequestIdRef = useRef(0)
  const listGenerationRef = useRef(0)
  const loadingMoreRef = useRef(false)
  const formSubmittingRef = useRef(false)
  const busyRecordRef = useRef<string | null>(null)
  const editEtagRef = useRef<string | null>(null)
  const createKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)
  const deleteKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)

  const queryFilters = useMemo<TransactionListFilters>(() => ({
    type: filters.type === ALL ? undefined : filters.type,
    category_id: filters.categoryId === ALL ? undefined : filters.categoryId,
    from: filters.from || undefined,
    to: filters.to || undefined,
    q: filters.search.trim() || undefined,
  }), [filters])

  const dateRangeInvalid = Boolean(
    filters.from && filters.to && filters.from > filters.to,
  )

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const loadCategories = useCallback(async () => {
    const requestId = ++categoryRequestIdRef.current
    setCategoriesLoading(true)
    setCategoriesError('')

    try {
      const result = await authorizedRequest((accessToken) =>
        fetchTransactionCategories(accessToken),
      )
      if (!mountedRef.current || requestId !== categoryRequestIdRef.current) {
        return
      }
      setCategories(result)
    } catch (error) {
      if (!mountedRef.current || requestId !== categoryRequestIdRef.current) {
        return
      }
      setCategoriesError(
        describeError(
          error,
          'Unable to load transaction categories. Please try again.',
          'Your session has expired. Sign in again to load categories.',
        ),
      )
    } finally {
      if (mountedRef.current && requestId === categoryRequestIdRef.current) {
        setCategoriesLoading(false)
      }
    }
  }, [authorizedRequest])

  const loadFirstPage = useCallback(async (query: TransactionListFilters) => {
    const requestId = ++listRequestIdRef.current
    const generation = ++listGenerationRef.current
    loadingMoreRef.current = false
    setListLoadingMore(false)
    setListLoading(true)
    setListError('')

    try {
      const page = await authorizedRequest((accessToken) =>
        fetchTransactions(accessToken, {
          ...query,
          limit: PAGE_SIZE,
        }),
      )
      if (
        !mountedRef.current
        || requestId !== listRequestIdRef.current
        || generation !== listGenerationRef.current
      ) {
        return
      }
      setTransactions(page.data)
      setNextCursor(page.pagination.next_cursor)
    } catch (error) {
      if (
        !mountedRef.current
        || requestId !== listRequestIdRef.current
        || generation !== listGenerationRef.current
      ) {
        return
      }
      setTransactions([])
      setNextCursor(null)
      setListError(
        describeError(
          error,
          'Unable to load transactions. Please try again.',
          'Your session has expired. Sign in again to view transactions.',
        ),
      )
    } finally {
      if (
        mountedRef.current
        && requestId === listRequestIdRef.current
        && generation === listGenerationRef.current
      ) {
        setListLoading(false)
      }
    }
  }, [authorizedRequest])

  useEffect(() => {
    if (!userId) {
      setCategoriesLoading(false)
      setCategoriesError('Your session is not available. Sign in again.')
      return
    }
    void loadCategories()
  }, [userId, loadCategories])

  useEffect(() => {
    if (!userId) {
      setListLoading(false)
      setListError('Your session is not available. Sign in again.')
      return
    }
    if (dateRangeInvalid) {
      listRequestIdRef.current += 1
      listGenerationRef.current += 1
      loadingMoreRef.current = false
      setListLoadingMore(false)
      setListLoading(false)
      setTransactions([])
      setNextCursor(null)
      setListError('The start date must not be after the end date.')
      return
    }
    void loadFirstPage(queryFilters)
  }, [dateRangeInvalid, loadFirstPage, queryFilters, userId])

  function resetForm() {
    setFormValues(blankForm(categories))
    setEditingId(null)
    setEditCategoryFallback(null)
    setFormError('')
    setFormNotice('')
    setFormOpen(false)
    editEtagRef.current = null
    createKeyRef.current = null
  }

  function beginAdd() {
    if (!canCreate) {
      setActionError('Transaction categories are not available yet.')
      return
    }
    if (formSubmittingRef.current || busyRecordRef.current !== null) return
    setFormValues(blankForm(categories))
    setEditingId(null)
    setEditCategoryFallback(null)
    setFormError('')
    setFormNotice('')
    setActionError('')
    setSuccessNotice('')
    editEtagRef.current = null
    createKeyRef.current = null
    setFormOpen(true)
  }

  function applyTransactionToForm(transaction: TransactionResource) {
    setFormValues({
      description: transaction.description,
      type: transaction.type,
      categoryId: transaction.category_id,
      amount: moneyToEntryString(transaction.amount_kyat),
      date: transaction.transaction_date,
    })
    setEditCategoryFallback(
      categories.some((category) => category.id === transaction.category_id)
        ? null
        : {
          id: transaction.category_id,
          name: transaction.category,
          type: transaction.type,
        },
    )
  }

  async function beginEdit(transaction: TransactionResource) {
    if (formSubmittingRef.current || busyRecordRef.current !== null) return
    if (categoriesLoading || categoriesError) {
      setActionError(
        'Transaction categories are still unavailable. Retry loading them before editing.',
      )
      return
    }

    busyRecordRef.current = transaction.id
    setBusyRecordId(transaction.id)
    setActionError('')
    setSuccessNotice('')
    setFormError('')
    setFormNotice('')

    try {
      const view = await authorizedRequest((accessToken) =>
        fetchTransaction(accessToken, transaction.id),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setActionError(
          'Unable to edit this transaction because the server did not provide a revision. Reload the page and try again.',
        )
        return
      }

      editEtagRef.current = view.etag
      applyTransactionToForm(view.transaction)
      setEditingId(view.transaction.id)
      setFormOpen(true)
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setActionError(
          'This transaction no longer exists. The list is being refreshed.',
        )
        void loadFirstPage(queryFilters)
        return
      }
      setActionError(
        describeError(
          error,
          'Unable to open this transaction for editing. Please try again.',
          'Your session has expired. Sign in again to edit transactions.',
        ),
      )
    } finally {
      busyRecordRef.current = null
      if (mountedRef.current) setBusyRecordId(null)
    }
  }

  async function reloadLatestForEdit(transactionId: string) {
    try {
      const view = await authorizedRequest((accessToken) =>
        fetchTransaction(accessToken, transactionId),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setFormError(
          'The latest revision was not provided. Close the form and reopen the transaction.',
        )
        return
      }
      editEtagRef.current = view.etag
      applyTransactionToForm(view.transaction)
      setEditingId(view.transaction.id)
      setFormOpen(true)
      setTransactions((current) => current.map((item) =>
        item.id === view.transaction.id ? view.transaction : item,
      ))
    } catch (error) {
      if (!mountedRef.current) return
      if (isApiError(error) && error.status === 404) {
        setFormError('This transaction no longer exists.')
        setFormOpen(false)
        setEditingId(null)
        void loadFirstPage(queryFilters)
        return
      }
      setFormError(
        describeError(
          error,
          'Unable to reload the latest transaction. Close the form and try again.',
          'Your session has expired. Sign in again to edit transactions.',
        ),
      )
    }
  }

  function handleTypeChange(type: TransactionType) {
    setFormValues((current) => {
      const currentCategory = categories.find(
        (category) => category.id === current.categoryId,
      )
      if (currentCategory?.type === type) {
        return { ...current, type }
      }
      return {
        ...current,
        type,
        categoryId: categories.find((category) => category.type === type)?.id ?? '',
      }
    })
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (formSubmittingRef.current) return

    setFormError('')
    setFormNotice('')
    setActionError('')
    setSuccessNotice('')

    const description = formValues.description.trim()
    const amountKyat = parseMoneyToKyat(formValues.amount, {
      maxAmount: MAX_FINANCIAL_ENTRY_KYAT,
    })
    const selectedCategory = formCategories.find(
      (category) => category.id === formValues.categoryId,
    )

    if (!description) {
      setFormError('Enter a description for this transaction.')
      return
    }
    if (description.length > 80) {
      setFormError('Description must be 80 characters or fewer.')
      return
    }
    if (amountKyat === null) {
      setFormError('Enter a positive whole-Kyat amount up to 1,000,000,000 MMK.')
      return
    }
    if (!selectedCategory || selectedCategory.type !== formValues.type) {
      setFormError('Choose a category that matches the transaction type.')
      return
    }
    if (!isValidDateOnly(formValues.date)) {
      setFormError('Choose a valid transaction date.')
      return
    }

    const input: TransactionCreateInput = {
      description,
      type: formValues.type,
      category_id: selectedCategory.id,
      amount_kyat: amountKyat,
      transaction_date: formValues.date,
    }

    formSubmittingRef.current = true
    setFormSubmitting(true)

    try {
      if (editingId) {
        const etag = editEtagRef.current
        if (!etag) {
          setFormError(
            'The current transaction revision is missing. Close the form and reopen the transaction.',
          )
          return
        }

        await authorizedRequest((accessToken) =>
          updateTransaction(accessToken, editingId, input, etag),
        )
        if (!mountedRef.current) return

        resetForm()
        setSuccessNotice('Transaction updated.')
        void loadFirstPage(queryFilters)
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
        createTransaction(
          accessToken,
          input,
          createKeyRef.current?.key ?? createIdempotencyKey(),
        ),
      )
      if (!mountedRef.current) return

      createKeyRef.current = null
      resetForm()
      setSuccessNotice('Transaction added.')
      void loadFirstPage(queryFilters)
    } catch (error) {
      if (!mountedRef.current) return

      if (
        editingId
        && isApiError(error)
        && (error.status === 412 || error.status === 428)
      ) {
        setFormNotice(
          'This transaction was changed elsewhere. The latest record has been reloaded; review it and save again.',
        )
        await reloadLatestForEdit(editingId)
        return
      }

      setFormError(
        describeError(
          error,
          editingId
            ? 'Unable to save this transaction. Please try again.'
            : 'Unable to add this transaction. Please try again.',
          'Your session has expired. Sign in again to save transactions.',
        ),
      )
    } finally {
      formSubmittingRef.current = false
      if (mountedRef.current) setFormSubmitting(false)
    }
  }

  async function handleDelete(transaction: TransactionResource) {
    if (formSubmittingRef.current || busyRecordRef.current !== null) return
    const confirmed = window.confirm(
      `Delete “${transaction.description}”? This permanently removes the transaction.`,
    )
    if (!confirmed) return

    busyRecordRef.current = transaction.id
    setBusyRecordId(transaction.id)
    setActionError('')
    setSuccessNotice('')

    try {
      const view = await authorizedRequest((accessToken) =>
        fetchTransaction(accessToken, transaction.id),
      )
      if (!mountedRef.current) return
      if (!view.etag) {
        setActionError(
          'Unable to delete this transaction because the server did not provide a revision. Reload the page and try again.',
        )
        return
      }

      const fingerprint = `${view.transaction.id}|${view.etag}`
      if (
        !deleteKeyRef.current
        || deleteKeyRef.current.fingerprint !== fingerprint
      ) {
        deleteKeyRef.current = {
          fingerprint,
          key: createIdempotencyKey(),
        }
      }

      await authorizedRequest((accessToken) =>
        deleteTransaction(
          accessToken,
          view.transaction.id,
          view.etag as string,
          deleteKeyRef.current?.key ?? createIdempotencyKey(),
        ),
      )
      if (!mountedRef.current) return

      if (deleteKeyRef.current?.fingerprint === fingerprint) {
        deleteKeyRef.current = null
      }
      setTransactions((current) =>
        current.filter((item) => item.id !== transaction.id),
      )
      if (editingId === transaction.id) resetForm()
      setSuccessNotice('Transaction deleted.')
    } catch (error) {
      if (!mountedRef.current) return

      if (isApiError(error) && error.status === 404) {
        setTransactions((current) =>
          current.filter((item) => item.id !== transaction.id),
        )
        if (editingId === transaction.id) resetForm()
        setActionError(
          'This transaction was already deleted. The list has been updated.',
        )
        return
      }

      if (isApiError(error) && (error.status === 412 || error.status === 428)) {
        setActionError(
          'This transaction was changed elsewhere. The latest record has been reloaded; review it and try deleting again.',
        )
        try {
          const latest = await authorizedRequest((accessToken) =>
            fetchTransaction(accessToken, transaction.id),
          )
          if (!mountedRef.current) return
          setTransactions((current) => current.map((item) =>
            item.id === latest.transaction.id ? latest.transaction : item,
          ))
          if (editingId === transaction.id) {
            editEtagRef.current = latest.etag
            applyTransactionToForm(latest.transaction)
          }
        } catch (reloadError) {
          if (!mountedRef.current) return
          if (isApiError(reloadError) && reloadError.status === 404) {
            setTransactions((current) =>
              current.filter((item) => item.id !== transaction.id),
            )
            if (editingId === transaction.id) resetForm()
          }
        }
        return
      }

      setActionError(
        describeError(
          error,
          'Unable to delete this transaction. Please try again.',
          'Your session has expired. Sign in again to delete transactions.',
        ),
      )
    } finally {
      busyRecordRef.current = null
      if (mountedRef.current) setBusyRecordId(null)
    }
  }

  async function loadMore() {
    if (!nextCursor || listLoading || loadingMoreRef.current) return

    const generation = listGenerationRef.current
    const cursor = nextCursor
    loadingMoreRef.current = true
    setListLoadingMore(true)
    setListError('')

    try {
      const page = await authorizedRequest((accessToken) =>
        fetchTransactions(accessToken, {
          ...queryFilters,
          limit: PAGE_SIZE,
          cursor,
        }),
      )
      if (!mountedRef.current || generation !== listGenerationRef.current) {
        return
      }
      setTransactions((current) => {
        const existingIds = new Set(current.map((item) => item.id))
        return [
          ...current,
          ...page.data.filter((item) => !existingIds.has(item.id)),
        ]
      })
      setNextCursor(page.pagination.next_cursor)
    } catch (error) {
      if (!mountedRef.current || generation !== listGenerationRef.current) {
        return
      }
      setListError(
        describeError(
          error,
          'Unable to load more transactions. Please try again.',
          'Your session has expired. Sign in again to view transactions.',
        ),
      )
    } finally {
      loadingMoreRef.current = false
      if (mountedRef.current && generation === listGenerationRef.current) {
        setListLoadingMore(false)
      }
    }
  }

  function handleFilterTypeChange(type: TypeFilter) {
    setFilters((current) => {
      const categoryStillMatches = current.categoryId === ALL
        || categories.some((category) =>
          category.id === current.categoryId
          && (type === ALL || category.type === type),
        )
      return {
        ...current,
        type,
        categoryId: categoryStillMatches ? current.categoryId : ALL,
      }
    })
  }

  const filterCategories = filters.type === ALL
    ? categories
    : categories.filter((category) => category.type === filters.type)

  const formCategories = useMemo(() => {
    const active = categories.filter(
      (category) => category.type === formValues.type,
    )
    if (
      editCategoryFallback
      && editCategoryFallback.type === formValues.type
      && !active.some((category) => category.id === editCategoryFallback.id)
    ) {
      return [...active, editCategoryFallback]
    }
    return active
  }, [categories, editCategoryFallback, formValues.type])

  const canCreate = !categoriesLoading
    && !categoriesError
    && categories.length > 0
  const hasActiveFilters = filters.type !== ALL
    || filters.categoryId !== ALL
    || filters.search.trim() !== ''
    || filters.from !== ''
    || filters.to !== ''
  const actionsDisabled = formSubmitting || busyRecordId !== null
  const editDisabled = actionsDisabled
    || categoriesLoading
    || Boolean(categoriesError)

  return (
    <section className="panel transaction-management" aria-labelledby="management-title">
      <div className="management-heading">
        <div>
          <p className="panel-overline">LIVE RECORDS · BACKEND</p>
          <h2 id="management-title">Transactions</h2>
          <p className="management-description">Manage your income and expenses. Changes are saved to your FinPath account.</p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={beginAdd}
          disabled={!canCreate || actionsDisabled}
        >
          <span aria-hidden="true">+</span> Add transaction
        </button>
      </div>

      {categoriesLoading && (
        <p className="management-description" role="status">Loading categories…</p>
      )}
      {categoriesError && (
        <p className="form-error" role="alert">
          {categoriesError}{' '}
          <button
            className="text-button"
            type="button"
            onClick={() => { void loadCategories() }}
          >
            Retry categories
          </button>
        </p>
      )}
      {!categoriesLoading && !categoriesError && categories.length === 0 && (
        <p className="form-error" role="alert">
          No active transaction categories are available. Contact an administrator before adding transactions.
        </p>
      )}

      <div className="transaction-filters" aria-label="Filter transactions">
        <label className="search-control">
          <span>Search description</span>
          <input
            type="search"
            value={filters.search}
            onChange={(event) => setFilters((current) => ({
              ...current,
              search: event.target.value,
            }))}
            placeholder="Find a transaction"
          />
        </label>
        <label>
          <span>Type</span>
          <select
            value={filters.type}
            onChange={(event) =>
              handleFilterTypeChange(event.target.value as TypeFilter)}
          >
            <option value="all">All types</option>
            <option value="income">Income</option>
            <option value="expense">Expense</option>
          </select>
        </label>
        <label>
          <span>Category</span>
          <select
            value={filters.categoryId}
            onChange={(event) => setFilters((current) => ({
              ...current,
              categoryId: event.target.value,
            }))}
            disabled={categoriesLoading || Boolean(categoriesError)}
          >
            <option value="all">All categories</option>
            {filterCategories.map((category) => (
              <option key={category.id} value={category.id}>{category.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>From</span>
          <input
            aria-label="From date"
            type="date"
            value={filters.from}
            onChange={(event) => setFilters((current) => ({
              ...current,
              from: event.target.value,
            }))}
          />
        </label>
        <label>
          <span>To</span>
          <input
            aria-label="To date"
            type="date"
            value={filters.to}
            onChange={(event) => setFilters((current) => ({
              ...current,
              to: event.target.value,
            }))}
          />
        </label>
      </div>

      {formOpen && (
        <form className="transaction-form" noValidate onSubmit={handleSubmit} aria-labelledby="transaction-form-title">
          <div className="form-heading">
            <h3 id="transaction-form-title">{editingId ? 'Edit transaction' : 'Add transaction'}</h3>
            <span>{editingId ? 'Live record' : 'New record'}</span>
          </div>
          {formError && <p className="form-error" role="alert">{formError}</p>}
          {formNotice && <p className="form-notice" role="status">{formNotice}</p>}
          <div className="form-fields">
            <label className="description-field">
              <span>Description <b aria-hidden="true">*</b></span>
              <input
                autoFocus
                required
                maxLength={80}
                disabled={formSubmitting}
                value={formValues.description}
                onChange={(event) => setFormValues({
                  ...formValues,
                  description: event.target.value,
                })}
                placeholder="e.g. Monthly salary"
              />
            </label>
            <label>
              <span>Type <b aria-hidden="true">*</b></span>
              <select
                disabled={formSubmitting}
                value={formValues.type}
                onChange={(event) =>
                  handleTypeChange(event.target.value as TransactionType)}
              >
                <option value="expense">Expense</option>
                <option value="income">Income</option>
              </select>
            </label>
            <label>
              <span>Category <b aria-hidden="true">*</b></span>
              <select
                required
                disabled={formSubmitting || formCategories.length === 0}
                value={formValues.categoryId}
                onChange={(event) => setFormValues({
                  ...formValues,
                  categoryId: event.target.value,
                })}
              >
                {formCategories.length === 0 && (
                  <option value="">No categories available</option>
                )}
                {formCategories.map((category) => (
                  <option key={category.id} value={category.id}>{category.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Amount (MMK) <b aria-hidden="true">*</b></span>
              <input
                required
                type="number"
                min="1"
                max={MAX_FINANCIAL_ENTRY_KYAT}
                step="1"
                inputMode="numeric"
                disabled={formSubmitting}
                value={formValues.amount}
                onChange={(event) => setFormValues({
                  ...formValues,
                  amount: event.target.value,
                })}
                placeholder="e.g. 150000"
              />
            </label>
            <label>
              <span>Date <b aria-hidden="true">*</b></span>
              <input
                required
                type="date"
                disabled={formSubmitting}
                value={formValues.date}
                onChange={(event) => setFormValues({
                  ...formValues,
                  date: event.target.value,
                })}
              />
            </label>
          </div>
          <div className="form-actions">
            <button
              className="text-button"
              type="button"
              onClick={resetForm}
              disabled={formSubmitting}
            >
              Cancel
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={formSubmitting || formCategories.length === 0}
            >
              {formSubmitting
                ? 'Saving…'
                : editingId ? 'Save changes' : 'Add transaction'}
            </button>
          </div>
        </form>
      )}

      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {successNotice && <p className="form-notice" role="status">{successNotice}</p>}

      <div className="managed-list-heading">
        <span>
          {listLoading
            ? 'Loading transactions…'
            : `${transactions.length} ${transactions.length === 1 ? 'transaction' : 'transactions'} loaded`}
        </span>
        <span>{nextCursor ? 'More available' : 'Live backend records'}</span>
      </div>

      {listError && (
        <div className="empty-transactions" role="alert">
          <strong>Unable to load transactions</strong>
          <span>{listError}</span>
          {!dateRangeInvalid && (
            <button
              className="text-button"
              type="button"
              onClick={() => { void loadFirstPage(queryFilters) }}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {listLoading ? (
        <div className="empty-transactions" role="status">
          <strong>Loading transactions…</strong>
          <span>Fetching the latest records from FinPath.</span>
        </div>
      ) : transactions.length > 0 ? (
        <div className="managed-table-wrap">
          <table className="managed-table">
            <thead>
              <tr><th scope="col">Description</th><th scope="col">Category</th><th scope="col">Date</th><th scope="col">Type</th><th scope="col" className="amount-heading">Amount</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
            </thead>
            <tbody>
              {transactions.map((transaction) => {
                const isBusy = busyRecordId === transaction.id
                return (
                  <tr key={transaction.id}>
                    <th scope="row">{transaction.description}</th>
                    <td data-label="Category">{transaction.category}</td>
                    <td data-label="Date">{formatTransactionDate(transaction.transaction_date)}</td>
                    <td data-label="Type"><span className={`type-badge ${transaction.type}`}>{transaction.type}</span></td>
                    <td data-label="Amount" className={`managed-amount ${transaction.type}`}>{formatTransactionAmount(transaction.amount_kyat)}</td>
                    <td className="row-actions">
                      <button
                        className="icon-button"
                        type="button"
                        aria-label={`Edit ${transaction.description}`}
                        onClick={() => { void beginEdit(transaction) }}
                        disabled={editDisabled}
                      >
                        {isBusy ? 'Opening…' : 'Edit'}
                      </button>
                      <button
                        className="icon-button delete-action"
                        type="button"
                        aria-label={`Delete ${transaction.description}`}
                        onClick={() => { void handleDelete(transaction) }}
                        disabled={actionsDisabled}
                      >
                        {isBusy ? 'Working…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : !listError ? (
        <div className="empty-transactions" role="status">
          <strong>No matching transactions</strong>
          <span>
            {hasActiveFilters
              ? 'Try another search or choose a different filter.'
              : 'Add your first transaction to get started.'}
          </span>
        </div>
      ) : null}

      {nextCursor && !listLoading && !listError && (
        <div className="form-actions">
          <button
            className="text-button"
            type="button"
            onClick={() => { void loadMore() }}
            disabled={listLoadingMore}
          >
            {listLoadingMore ? 'Loading more…' : 'Load more transactions'}
          </button>
        </div>
      )}
    </section>
  )
}

export default TransactionsSection
