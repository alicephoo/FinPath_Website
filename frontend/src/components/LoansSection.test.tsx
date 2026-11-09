import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LoansSection from './LoansSection'
import { ApiError } from '../api/errors'
import type {
  LoanResource,
  LoanView,
  RepaymentResource,
  RepaymentView,
} from '../api/loans'

const mocks = vi.hoisted(() => ({
  authorizedRequest: vi.fn(),
  fetchLoans: vi.fn(),
  fetchLoan: vi.fn(),
  createLoan: vi.fn(),
  updateLoan: vi.fn(),
  fetchRepayments: vi.fn(),
  createRepayment: vi.fn(),
  createIdempotencyKey: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'alex@example.com' },
    authorizedRequest: mocks.authorizedRequest,
  }),
}))

vi.mock('../api/loans', () => ({
  fetchLoans: mocks.fetchLoans,
  fetchLoan: mocks.fetchLoan,
  createLoan: mocks.createLoan,
  updateLoan: mocks.updateLoan,
  fetchRepayments: mocks.fetchRepayments,
  createRepayment: mocks.createRepayment,
  fetchRepayment: vi.fn(),
  correctRepayment: vi.fn(),
}))

vi.mock('../auth/api', () => ({
  createIdempotencyKey: mocks.createIdempotencyKey,
}))

const loan: LoanResource = {
  id: 'loan-1',
  name: 'Workshop loan',
  principal_kyat: 180_000_000,
  start_date: '2022-05-12',
  total_repaid_kyat: 17_750_000,
  outstanding_principal_kyat: 162_250_000,
  created_at: '2022-05-12T09:00:00+00:00',
  updated_at: '2026-11-03T09:00:00+00:00',
}

function repaymentFixture(
  overrides: Partial<RepaymentResource> = {},
): RepaymentResource {
  return {
    id: 'repayment-1',
    amount_kyat: 12_000_000,
    repayment_date: '2026-10-01',
    note: 'Monthly payment',
    created_at: '2026-10-01T09:00:00+00:00',
    corrected: false,
    ...overrides,
  }
}

function loanView(overrides: Partial<LoanResource> = {}, etag = '"1"'): LoanView {
  return { loan: { ...loan, ...overrides }, etag }
}

function repaymentView(
  overrides: Partial<RepaymentResource> = {},
  etag = '"1"',
): RepaymentView {
  return { repayment: repaymentFixture(overrides), etag }
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

function loanForm(): HTMLFormElement {
  return document.querySelector('.loan-form') as HTMLFormElement
}

function repaymentForm(): HTMLFormElement {
  return document.querySelector('.repayment-form') as HTMLFormElement
}

function record(name = loan.name): HTMLElement {
  return screen.getByRole('article', { name })
}

function openHistory(article = record()): void {
  const details = article.querySelector(
    '.repayment-history',
  ) as HTMLDetailsElement
  details.open = true
  fireEvent(details, new Event('toggle'))
}

async function renderLoaded(loans: LoanResource[] = [loan]) {
  mocks.fetchLoans.mockResolvedValue(loans)
  render(<LoansSection />)
  if (loans.length > 0) {
    await screen.findByText(loans[0].name)
  } else {
    await screen.findByText('No loans added')
  }
}

function openCreateForm(): HTMLFormElement {
  fireEvent.click(screen.getByRole('button', { name: /Add loan/ }))
  return loanForm()
}

function fillLoanForm(
  form: HTMLFormElement,
  values: {
    name?: string
    principal?: string
    startDate?: string
  } = {},
): void {
  fireEvent.change(within(form).getByLabelText(/Loan name/), {
    target: { value: values.name ?? 'Car loan' },
  })
  fireEvent.change(within(form).getByLabelText(/Original principal/), {
    target: { value: values.principal ?? '125500' },
  })
  fireEvent.change(within(form).getByLabelText(/Start date/), {
    target: { value: values.startDate ?? '2026-11-03' },
  })
}

function openRepaymentForm(article = record()): HTMLFormElement {
  fireEvent.click(within(article).getByRole('button', {
    name: 'Record repayment',
  }))
  return repaymentForm()
}

function fillRepaymentForm(
  form: HTMLFormElement,
  values: {
    amount?: string
    date?: string
    note?: string
  } = {},
): void {
  fireEvent.change(within(form).getByLabelText(/Amount/), {
    target: { value: values.amount ?? '2500' },
  })
  fireEvent.change(within(form).getByLabelText(/Date/), {
    target: { value: values.date ?? '2026-11-03' },
  })
  if (values.note) {
    fireEvent.change(within(form).getByLabelText(/Note/), {
      target: { value: values.note },
    })
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.authorizedRequest.mockImplementation(
    (request: (accessToken: string) => Promise<unknown>) =>
      request('access-token-1'),
  )
  mocks.fetchLoans.mockResolvedValue([loan])
  mocks.fetchRepayments.mockResolvedValue([])
  mocks.createIdempotencyKey.mockReturnValue('key-1')
})

describe('LoansSection backend integration', () => {
  it('loads backend loans and displays backend-calculated principal values', async () => {
    await renderLoaded([loan, {
      ...loan,
      id: 'loan-2',
      name: 'Vehicle loan',
      principal_kyat: 18_000_000,
      total_repaid_kyat: 4_500_000,
      outstanding_principal_kyat: 13_500_000,
      start_date: '2023-08-20',
    }])

    expect(mocks.fetchLoans).toHaveBeenCalledWith('access-token-1')
    expect(screen.getByText('LIVE RECORDS · BACKEND')).toBeInTheDocument()
    expect(screen.queryByText(/SAMPLE RECORDS/)).not.toBeInTheDocument()

    const home = record('Workshop loan')
    expect(within(home).getByText('180,000,000 MMK')).toBeInTheDocument()
    expect(within(home).getByText('17,750,000 MMK')).toBeInTheDocument()
    expect(within(home).getByText('162,250,000 MMK')).toBeInTheDocument()
    expect(within(home).getByText(/Started May 12, 2022/)).toBeInTheDocument()

    const education = record('Vehicle loan')
    expect(within(education).getByText('13,500,000 MMK')).toBeInTheDocument()
    expect(screen.queryByText('Northstar Studio')).not.toBeInTheDocument()
  })

  it('loads repayment history for a selected loan only when opened', async () => {
    mocks.fetchRepayments.mockResolvedValue([
      repaymentFixture(),
      repaymentFixture({
        id: 'repayment-2',
        amount_kyat: 5_750_000,
        repayment_date: '2026-09-01',
        note: 'Earlier payment',
        corrected: true,
      }),
    ])
    await renderLoaded()

    expect(mocks.fetchRepayments).not.toHaveBeenCalled()
    openHistory()

    expect(await screen.findByText('Monthly payment')).toBeInTheDocument()
    expect(screen.getByText('Earlier payment')).toBeInTheDocument()
    expect(screen.getByText(/Reversed by correction/)).toBeInTheDocument()
    expect(mocks.fetchRepayments).toHaveBeenCalledWith(
      'access-token-1',
      loan.id,
    )
    expect(screen.getByText('2 entries')).toBeInTheDocument()
  })

  it('shows repayment history loading, empty, and error states', async () => {
    const history = deferred<RepaymentResource[]>()
    mocks.fetchRepayments.mockReturnValueOnce(history.promise)
    await renderLoaded()

    openHistory()
    expect(screen.getByText('Loading repayments…')).toBeInTheDocument()

    history.resolve([])
    expect(await screen.findByText('No repayments recorded yet.'))
      .toBeInTheDocument()

    mocks.fetchRepayments.mockRejectedValueOnce(new ApiError({
      status: 500,
      code: 'server_error',
      message: 'Repayment history is unavailable.',
    }))
    openHistory()
    expect(await screen.findByText('Repayment history is unavailable.'))
      .toBeInTheDocument()

    mocks.fetchRepayments.mockResolvedValueOnce([repaymentFixture()])
    fireEvent.click(screen.getByRole('button', { name: 'Retry history' }))
    expect(await screen.findByText('Monthly payment')).toBeInTheDocument()
  })

  it('shows a loading state, then an empty loan list', async () => {
    const loans = deferred<LoanResource[]>()
    mocks.fetchLoans.mockReturnValueOnce(loans.promise)

    render(<LoansSection />)
    expect(screen.getByText('Loading loans…')).toBeInTheDocument()

    loans.resolve([])
    expect(await screen.findByText('No loans added')).toBeInTheDocument()
    expect(screen.getByText(/Add a loan to track principal/)).toBeInTheDocument()
  })

  it('shows an authentication error and retries the loan list', async () => {
    mocks.fetchLoans
      .mockRejectedValueOnce(new ApiError({
        status: 401,
        code: 'authentication_required',
        message: 'Authentication is required.',
      }))
      .mockResolvedValueOnce([loan])

    render(<LoansSection />)

    expect(await screen.findByText(
      /Your session has expired. Sign in again to view loans/,
    )).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Workshop loan')).toBeInTheDocument()
    expect(mocks.fetchLoans).toHaveBeenCalledTimes(2)
  })

  it('creates a loan with an idempotency key and blocks duplicate submissions', async () => {
    const created = {
      ...loan,
      id: 'loan-new',
      name: 'Car loan',
      principal_kyat: 125_500,
      total_repaid_kyat: 0,
      outstanding_principal_kyat: 125_500,
    }
    mocks.fetchLoans
      .mockResolvedValueOnce([])
      .mockResolvedValue([created])
    render(<LoansSection />)
    await screen.findByText('No loans added')

    const form = openCreateForm()
    fillLoanForm(form, { name: 'Car loan', principal: '125500' })
    const pending = deferred<LoanView>()
    mocks.createLoan.mockReturnValue(pending.promise)

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createLoan).toHaveBeenCalledOnce()
    })
    expect(mocks.createIdempotencyKey).toHaveBeenCalledOnce()
    expect(mocks.createLoan).toHaveBeenCalledWith(
      'access-token-1',
      {
        name: 'Car loan',
        principal_kyat: 125_500,
        start_date: '2026-11-03',
      },
      'key-1',
    )

    pending.resolve(loanView({ ...created }, '"1"'))
    expect(await screen.findByText('Loan added.')).toBeInTheDocument()
    expect(await screen.findByText('Car loan')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add loan details' }))
      .not.toBeInTheDocument()
  })

  it('reuses the create key for an identical retry and changes it when the payload changes', async () => {
    mocks.createIdempotencyKey
      .mockReturnValueOnce('key-1')
      .mockReturnValueOnce('key-2')
    const networkError = new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API.',
    })
    const created = {
      ...loan,
      id: 'loan-retry',
      name: 'Retry loan',
      principal_kyat: 43_000,
      total_repaid_kyat: 0,
      outstanding_principal_kyat: 43_000,
    }
    mocks.fetchLoans
      .mockResolvedValueOnce([])
      .mockResolvedValue([created])
    mocks.createLoan
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(loanView(created, '"1"'))

    render(<LoansSection />)
    await screen.findByText('No loans added')
    const form = openCreateForm()
    fillLoanForm(form, { name: 'Retry loan', principal: '42000' })

    fireEvent.submit(form)
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createLoan).toHaveBeenCalledTimes(2)
    })

    fireEvent.change(within(form).getByLabelText(/Original principal/), {
      target: { value: '43000' },
    })
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createLoan).toHaveBeenCalledTimes(3)
    })

    expect(mocks.createLoan.mock.calls.map((call) => call[2])).toEqual([
      'key-1',
      'key-1',
      'key-2',
    ])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledTimes(2)
    await screen.findByText('Loan added.')
  })

  it('fetches the current ETag before editing and sends If-Match', async () => {
    const updated = { ...loan, name: 'Updated workshop loan' }
    mocks.fetchLoans
      .mockResolvedValueOnce([loan])
      .mockResolvedValue([updated])
    mocks.fetchLoan.mockResolvedValue(loanView({}, '"2"'))
    mocks.updateLoan.mockResolvedValue(loanView(updated, '"3"'))
    render(<LoansSection />)
    await screen.findByText('Workshop loan')

    fireEvent.click(within(record()).getByRole('button', {
      name: 'Edit details',
    }))
    const form = await waitFor(() => {
      screen.getByRole('heading', { name: 'Edit loan details' })
      return loanForm()
    })

    expect(mocks.fetchLoan).toHaveBeenCalledWith('access-token-1', loan.id)
    fireEvent.change(within(form).getByLabelText(/Loan name/), {
      target: { value: 'Updated workshop loan' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save loan',
    }))

    await waitFor(() => {
      expect(mocks.updateLoan).toHaveBeenCalledWith(
        'access-token-1',
        loan.id,
        {
          name: 'Updated workshop loan',
          principal_kyat: 180_000_000,
          start_date: '2022-05-12',
        },
        '"2"',
      )
    })
    expect(await screen.findByText('Loan updated.')).toBeInTheDocument()
    expect(await screen.findByText('Updated workshop loan')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Edit loan details' }))
      .not.toBeInTheDocument()
  })

  it.each([412, 428])(
    'reloads the latest loan instead of overwriting a %i conflict',
    async (status) => {
      const latest = {
        ...loan,
        name: 'Changed elsewhere',
        principal_kyat: 175_000_000,
        outstanding_principal_kyat: 157_250_000,
      }
      mocks.fetchLoans.mockResolvedValueOnce([loan])
      mocks.fetchLoan
        .mockResolvedValueOnce(loanView({}, '"2"'))
        .mockResolvedValueOnce(loanView(latest, '"3"'))
      mocks.updateLoan.mockRejectedValue(new ApiError({
        status,
        code: status === 412 ? 'stale_revision' : 'precondition_required',
        message: 'The supplied revision does not match the current loan.',
      }))

      render(<LoansSection />)
      await screen.findByText('Workshop loan')
      fireEvent.click(within(record()).getByRole('button', {
        name: 'Edit details',
      }))
      const form = await waitFor(() => {
        screen.getByRole('heading', { name: 'Edit loan details' })
        return loanForm()
      })

      fireEvent.change(within(form).getByLabelText(/Loan name/), {
        target: { value: 'My local change' },
      })
      fireEvent.click(within(form).getByRole('button', {
        name: 'Save loan',
      }))

      expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
      await waitFor(() => {
        expect(mocks.fetchLoan).toHaveBeenCalledTimes(2)
      })
      expect(mocks.updateLoan).toHaveBeenCalledOnce()
      expect(within(form).getByLabelText(/Loan name/)).toHaveValue(
        'Changed elsewhere',
      )
      expect(within(form).getByLabelText(/Original principal/)).toHaveValue(
        175_000_000,
      )
      expect(screen.getByRole('heading', { name: 'Edit loan details' }))
        .toBeInTheDocument()
    },
  )

  it('rejects a principal below recorded repayments and an over-limit amount', async () => {
    await renderLoaded()
    mocks.fetchLoan.mockResolvedValue(loanView({}, '"2"'))
    fireEvent.click(within(record()).getByRole('button', {
      name: 'Edit details',
    }))
    const form = await waitFor(() => {
      screen.getByRole('heading', { name: 'Edit loan details' })
      return loanForm()
    })

    fireEvent.change(within(form).getByLabelText(/Original principal/), {
      target: { value: '1000' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save loan',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /Principal cannot be less than repayments already recorded/,
    )
    expect(mocks.updateLoan).not.toHaveBeenCalled()

    fireEvent.change(within(form).getByLabelText(/Original principal/), {
      target: { value: '1000000001' },
    })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save loan',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /up to 1,000,000,000 MMK/,
    )
    expect(mocks.updateLoan).not.toHaveBeenCalled()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    const repayment = openRepaymentForm()
    fireEvent.change(within(repayment).getByLabelText(/Amount/), {
      target: { value: '162250001' },
    })
    fireEvent.click(within(repayment).getByRole('button', {
      name: 'Save repayment',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /cannot exceed the outstanding principal/,
    )
    expect(mocks.createRepayment).not.toHaveBeenCalled()
  })

  it('records a repayment with an idempotency key, blocks duplicates, and refreshes the balance', async () => {
    const updated = {
      ...loan,
      total_repaid_kyat: 37_750_000,
      outstanding_principal_kyat: 142_250_000,
    }
    mocks.fetchLoans
      .mockResolvedValueOnce([loan])
      .mockResolvedValue([updated])
    mocks.fetchRepayments.mockResolvedValue([
      repaymentFixture({
        id: 'repayment-new',
        amount_kyat: 20_000_000,
        note: 'Partial payment',
      }),
    ])
    render(<LoansSection />)
    await screen.findByText('Workshop loan')

    const form = openRepaymentForm()
    fillRepaymentForm(form, {
      amount: '20000000',
      note: 'Partial payment',
    })
    const pending = deferred<RepaymentView>()
    mocks.createRepayment.mockReturnValue(pending.promise)

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createRepayment).toHaveBeenCalledOnce()
    })
    expect(mocks.createRepayment).toHaveBeenCalledWith(
      'access-token-1',
      loan.id,
      {
        amount_kyat: 20_000_000,
        repayment_date: '2026-11-03',
        note: 'Partial payment',
      },
      'key-1',
    )

    pending.resolve(repaymentView({
      id: 'repayment-new',
      amount_kyat: 20_000_000,
      note: 'Partial payment',
    }, '"2"'))
    expect(await screen.findByText('Repayment recorded.')).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.fetchLoans).toHaveBeenCalledTimes(2)
    })
    expect(mocks.fetchRepayments).toHaveBeenCalledWith(
      'access-token-1',
      loan.id,
    )
    expect(await screen.findByText('Partial payment')).toBeInTheDocument()
    expect(within(record()).getByText('142,250,000 MMK')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Record repayment' }))
      .not.toBeInTheDocument()
  })

  it('reuses a repayment idempotency key only for an unchanged retry', async () => {
    mocks.createIdempotencyKey
      .mockReturnValueOnce('key-1')
      .mockReturnValueOnce('key-2')
    const networkError = new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API.',
    })
    mocks.createRepayment
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(repaymentView())
    await renderLoaded()

    const form = openRepaymentForm()
    fillRepaymentForm(form, { amount: '2500', note: 'First note' })

    fireEvent.submit(form)
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createRepayment).toHaveBeenCalledTimes(2)
    })

    fireEvent.change(within(form).getByLabelText(/Note/), {
      target: { value: 'Changed note' },
    })
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createRepayment).toHaveBeenCalledTimes(3)
    })

    expect(mocks.createRepayment.mock.calls.map((call) => call[3])).toEqual([
      'key-1',
      'key-1',
      'key-2',
    ])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledTimes(2)
    expect(await screen.findByText('Repayment recorded.')).toBeInTheDocument()
  })

  it('shows backend overpayment and validation errors without closing the form', async () => {
    await renderLoaded()
    mocks.createRepayment.mockRejectedValue(new ApiError({
      status: 409,
      code: 'repayment_exceeds_principal',
      message: 'The repayment exceeds the loan\'s outstanding principal.',
      fieldErrors: [{
        field: 'amount_kyat',
        code: 'repayment_exceeds_principal',
        message: 'Enter an amount no greater than the outstanding principal.',
      }],
    }))

    const form = openRepaymentForm()
    fillRepaymentForm(form, { amount: '2500' })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save repayment',
    }))

    expect(await screen.findByText(
      'Enter an amount no greater than the outstanding principal.',
    )).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Record repayment' }))
      .toBeInTheDocument()
    expect(mocks.createRepayment).toHaveBeenCalledOnce()
  })

  it('rejects decimal and unsafe loan or repayment amounts without a request', async () => {
    await renderLoaded()
    const form = openCreateForm()

    fillLoanForm(form, { principal: '125.50' })
    fireEvent.click(within(form).getByRole('button', { name: 'Add loan' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat principal/,
    )
    expect(mocks.createLoan).not.toHaveBeenCalled()

    fireEvent.change(within(form).getByLabelText(/Original principal/), {
      target: { value: '1000000001' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Add loan' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /up to 1,000,000,000 MMK/,
    )
    expect(mocks.createLoan).not.toHaveBeenCalled()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    const repayment = openRepaymentForm()
    fireEvent.change(within(repayment).getByLabelText(/Amount/), {
      target: { value: '25.50' },
    })
    fireEvent.click(within(repayment).getByRole('button', {
      name: 'Save repayment',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat repayment/,
    )
    expect(mocks.createRepayment).not.toHaveBeenCalled()
  })
})
