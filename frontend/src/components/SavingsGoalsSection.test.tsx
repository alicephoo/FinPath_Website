import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SavingsGoalsSection from './SavingsGoalsSection'
import { ApiError } from '../api/errors'
import type {
  SavingsEntryResource,
  SavingsGoalResource,
  SavingsGoalView,
} from '../api/savings'

const mocks = vi.hoisted(() => ({
  authorizedRequest: vi.fn(),
  fetchSavingsGoals: vi.fn(),
  fetchSavingsGoal: vi.fn(),
  createSavingsGoal: vi.fn(),
  updateSavingsGoal: vi.fn(),
  fetchSavingsEntries: vi.fn(),
  createSavingsContribution: vi.fn(),
  createSavingsWithdrawal: vi.fn(),
  createIdempotencyKey: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', email: 'alex@example.com' },
    authorizedRequest: mocks.authorizedRequest,
  }),
}))

vi.mock('../api/savings', () => ({
  fetchSavingsGoals: mocks.fetchSavingsGoals,
  fetchSavingsGoal: mocks.fetchSavingsGoal,
  createSavingsGoal: mocks.createSavingsGoal,
  updateSavingsGoal: mocks.updateSavingsGoal,
  fetchSavingsEntries: mocks.fetchSavingsEntries,
  createSavingsContribution: mocks.createSavingsContribution,
  createSavingsWithdrawal: mocks.createSavingsWithdrawal,
}))

vi.mock('../auth/api', () => ({
  createIdempotencyKey: mocks.createIdempotencyKey,
}))

const goal: SavingsGoalResource = {
  id: 'goal-1',
  name: 'Rainy day fund',
  target_kyat: 12_000_000,
  tone: 'mint',
  status: 'active',
  actual_balance_kyat: 8_400_000,
  remaining_kyat: 3_600_000,
  created_at: '2026-10-01T09:00:00+00:00',
  updated_at: '2026-11-03T09:00:00+00:00',
}

function entryFixture(
  overrides: Partial<SavingsEntryResource> = {},
): SavingsEntryResource {
  return {
    id: 'entry-1',
    entry_type: 'contribution',
    amount_kyat: 100_000,
    entry_date: '2026-11-03',
    note: 'Monthly saving',
    plan_day_id: null,
    correction_of_entry_id: null,
    correction_reason: null,
    created_at: '2026-11-03T09:00:00+00:00',
    ...overrides,
  }
}

function goalView(
  overrides: Partial<SavingsGoalResource> = {},
  etag = '"1"',
): SavingsGoalView {
  return { goal: { ...goal, ...overrides }, etag }
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

function goalForm(): HTMLFormElement {
  return document.querySelector('.savings-goal-form') as HTMLFormElement
}

function entryForm(): HTMLFormElement {
  return document.querySelector('.savings-entry-form') as HTMLFormElement
}

function card(name = goal.name): HTMLElement {
  return screen.getByRole('article', { name })
}

async function openHistory(article = card()): Promise<void> {
  const details = article.querySelector(
    '.savings-history',
  ) as HTMLDetailsElement
  const callsBefore = mocks.fetchSavingsEntries.mock.calls.length
  if (details.open) {
    // jsdom only queues a native `toggle` when the `open` attribute changes.
    // The element is already open here, so request the history again
    // explicitly; no native event will be queued, so this fires exactly once.
    fireEvent(details, new Event('toggle'))
  } else {
    // Changing `open` makes jsdom queue exactly one native `toggle` event,
    // which React listens for on the element. Dispatching another one here
    // would start a duplicate request and race the assertions.
    details.open = true
  }
  await waitFor(() => {
    expect(mocks.fetchSavingsEntries.mock.calls.length).toBe(callsBefore + 1)
  })
}

async function renderLoaded(goals: SavingsGoalResource[] = [goal]) {
  mocks.fetchSavingsGoals.mockResolvedValue(goals)
  render(<SavingsGoalsSection />)
  if (goals.length > 0) {
    await screen.findByText(goals[0].name)
  } else {
    await screen.findByText('No savings goals yet')
  }
}

function openCreateForm(): HTMLFormElement {
  fireEvent.click(screen.getByRole('button', { name: 'Add goal' }))
  return goalForm()
}

function fillGoalForm(
  form: HTMLFormElement,
  values: {
    name?: string
    target?: string
    tone?: 'mint' | 'gold' | 'blue'
  } = {},
): void {
  fireEvent.change(within(form).getByLabelText(/Goal name/), {
    target: { value: values.name ?? 'Emergency fund' },
  })
  fireEvent.change(within(form).getByLabelText(/Target amount/), {
    target: { value: values.target ?? '5000000' },
  })
  fireEvent.change(within(form).getByLabelText(/Tone/), {
    target: { value: values.tone ?? 'mint' },
  })
}

function openContributionForm(article = card()): HTMLFormElement {
  fireEvent.click(within(article).getByRole('button', {
    name: 'Add contribution',
  }))
  return entryForm()
}

function openWithdrawalForm(article = card()): HTMLFormElement {
  fireEvent.click(within(article).getByRole('button', {
    name: 'Withdraw',
  }))
  return entryForm()
}

function fillEntryForm(
  form: HTMLFormElement,
  values: {
    amount?: string
    date?: string
    note?: string
  } = {},
): void {
  fireEvent.change(within(form).getByLabelText(/Amount/), {
    target: { value: values.amount ?? '200000' },
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
  mocks.fetchSavingsGoals.mockResolvedValue([goal])
  mocks.fetchSavingsEntries.mockResolvedValue([])
  mocks.createIdempotencyKey.mockReturnValue('key-1')
})

describe('SavingsGoalsSection backend integration', () => {
  it('loads backend goals and displays backend-calculated values', async () => {
    await renderLoaded([goal, {
      ...goal,
      id: 'goal-2',
      name: 'New camera',
      target_kyat: 2_000_000,
      actual_balance_kyat: 500_000,
      remaining_kyat: 1_500_000,
      tone: 'blue',
    }])

    expect(mocks.fetchSavingsGoals).toHaveBeenCalledWith('access-token-1')
    expect(screen.getByText('LIVE RECORDS · BACKEND')).toBeInTheDocument()
    expect(screen.queryByText(/SAMPLE RECORDS/)).not.toBeInTheDocument()

    const rainyDay = card('Rainy day fund')
    expect(within(rainyDay).getByText('8,400,000 MMK')).toBeInTheDocument()
    expect(within(rainyDay).getByText('12,000,000 MMK')).toBeInTheDocument()
    expect(within(rainyDay).getByText('3,600,000 MMK')).toBeInTheDocument()
    expect(within(rainyDay).getByText('70% complete')).toBeInTheDocument()

    const camera = card('New camera')
    expect(within(camera).getByText('500,000 MMK')).toBeInTheDocument()
    expect(within(camera).getByText('1,500,000 MMK')).toBeInTheDocument()

    expect(screen.getByText(
      /Corrections, archive\/restore, and daily savings plans are not available/,
    )).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(within(card()).getByRole('button', {
      name: 'Withdraw',
    })).toBeInTheDocument()
    expect(screen.queryByRole('button', {
      name: 'Create daily plan',
    })).not.toBeInTheDocument()
    expect(screen.queryByText('Northstar Studio')).not.toBeInTheDocument()
  })

  it('shows a loading state, then an empty savings list', async () => {
    const goals = deferred<SavingsGoalResource[]>()
    mocks.fetchSavingsGoals.mockReturnValueOnce(goals.promise)

    render(<SavingsGoalsSection />)
    expect(screen.getByText('Loading savings goals…')).toBeInTheDocument()

    goals.resolve([])
    expect(await screen.findByText('No savings goals yet')).toBeInTheDocument()
    expect(screen.getByText(/Add a goal to start tracking/)).toBeInTheDocument()
  })

  it('shows an authentication error and retries the savings list', async () => {
    mocks.fetchSavingsGoals
      .mockRejectedValueOnce(new ApiError({
        status: 401,
        code: 'authentication_required',
        message: 'Authentication is required.',
      }))
      .mockResolvedValueOnce([goal])

    render(<SavingsGoalsSection />)

    expect(await screen.findByText(
      /Your session has expired. Sign in again to view savings goals/,
    )).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Rainy day fund')).toBeInTheDocument()
    expect(mocks.fetchSavingsGoals).toHaveBeenCalledTimes(2)
  })

  it('creates a goal with an idempotency key and blocks duplicate submissions', async () => {
    const created = {
      ...goal,
      id: 'goal-new',
      name: 'Emergency fund',
      target_kyat: 5_000_000,
      tone: 'gold',
      actual_balance_kyat: 0,
      remaining_kyat: 5_000_000,
    }
    mocks.fetchSavingsGoals
      .mockResolvedValueOnce([])
      .mockResolvedValue([created])
    render(<SavingsGoalsSection />)
    await screen.findByText('No savings goals yet')

    const form = openCreateForm()
    fillGoalForm(form, {
      name: 'Emergency fund',
      target: '5000000',
      tone: 'gold',
    })
    const pending = deferred<SavingsGoalView>()
    mocks.createSavingsGoal.mockReturnValue(pending.promise)

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createSavingsGoal).toHaveBeenCalledOnce()
    })
    expect(mocks.createIdempotencyKey).toHaveBeenCalledOnce()
    expect(mocks.createSavingsGoal).toHaveBeenCalledWith(
      'access-token-1',
      {
        name: 'Emergency fund',
        target_kyat: 5_000_000,
        tone: 'gold',
      },
      'key-1',
    )

    pending.resolve(goalView({
      ...created,
    }, '"1"'))
    expect(await screen.findByText('Savings goal added.')).toBeInTheDocument()
    expect(await screen.findByText('Emergency fund')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add savings goal' }))
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
      ...goal,
      id: 'goal-retry',
      name: 'Retry goal',
      target_kyat: 43_000,
      actual_balance_kyat: 0,
      remaining_kyat: 43_000,
    }
    mocks.fetchSavingsGoals
      .mockResolvedValueOnce([])
      .mockResolvedValue([created])
    mocks.createSavingsGoal
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(goalView(created, '"1"'))

    render(<SavingsGoalsSection />)
    await screen.findByText('No savings goals yet')
    const form = openCreateForm()
    fillGoalForm(form, { name: 'Retry goal', target: '42000' })

    fireEvent.submit(form)
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createSavingsGoal).toHaveBeenCalledTimes(2)
    })

    fireEvent.change(within(form).getByLabelText(/Target amount/), {
      target: { value: '43000' },
    })
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createSavingsGoal).toHaveBeenCalledTimes(3)
    })

    expect(mocks.createSavingsGoal.mock.calls.map((call) => call[2])).toEqual([
      'key-1',
      'key-1',
      'key-2',
    ])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledTimes(2)
    await screen.findByText('Savings goal added.')
  })

  it('fetches the current ETag before editing and sends If-Match', async () => {
    const updated = {
      ...goal,
      name: 'Updated rainy day fund',
      tone: 'gold',
    }
    mocks.fetchSavingsGoals
      .mockResolvedValueOnce([goal])
      .mockResolvedValue([updated])
    mocks.fetchSavingsGoal.mockResolvedValue(goalView({}, '"2"'))
    mocks.updateSavingsGoal.mockResolvedValue(goalView(updated, '"3"'))
    render(<SavingsGoalsSection />)
    await screen.findByText('Rainy day fund')

    fireEvent.click(within(card()).getByRole('button', { name: 'Edit' }))
    const form = await waitFor(() => {
      screen.getByRole('heading', { name: 'Edit savings goal' })
      return goalForm()
    })

    expect(mocks.fetchSavingsGoal).toHaveBeenCalledWith(
      'access-token-1',
      goal.id,
    )
    fireEvent.change(within(form).getByLabelText(/Goal name/), {
      target: { value: 'Updated rainy day fund' },
    })
    fireEvent.change(within(form).getByLabelText(/Tone/), {
      target: { value: 'gold' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Save goal' }))

    await waitFor(() => {
      expect(mocks.updateSavingsGoal).toHaveBeenCalledWith(
        'access-token-1',
        goal.id,
        {
          name: 'Updated rainy day fund',
          target_kyat: 12_000_000,
          tone: 'gold',
        },
        '"2"',
      )
    })
    expect(await screen.findByText('Savings goal updated.')).toBeInTheDocument()
    expect(await screen.findByText('Updated rainy day fund')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Edit savings goal' }))
      .not.toBeInTheDocument()
  })

  it.each([412, 428])(
    'reloads the latest goal instead of overwriting a %i conflict',
    async (status) => {
      const latest = {
        ...goal,
        name: 'Changed elsewhere',
        target_kyat: 15_000_000,
        remaining_kyat: 6_600_000,
      }
      mocks.fetchSavingsGoals.mockResolvedValueOnce([goal])
      mocks.fetchSavingsGoal
        .mockResolvedValueOnce(goalView({}, '"2"'))
        .mockResolvedValueOnce(goalView(latest, '"3"'))
      mocks.updateSavingsGoal.mockRejectedValue(new ApiError({
        status,
        code: status === 412 ? 'stale_revision' : 'precondition_required',
        message: 'The supplied revision does not match the current savings goal.',
      }))

      render(<SavingsGoalsSection />)
      await screen.findByText('Rainy day fund')
      fireEvent.click(within(card()).getByRole('button', { name: 'Edit' }))
      const form = await waitFor(() => {
        screen.getByRole('heading', { name: 'Edit savings goal' })
        return goalForm()
      })

      fireEvent.change(within(form).getByLabelText(/Goal name/), {
        target: { value: 'My local change' },
      })
      fireEvent.click(within(form).getByRole('button', { name: 'Save goal' }))

      expect(await screen.findByText(
        /This goal was changed elsewhere/,
      )).toBeInTheDocument()
      await waitFor(() => {
        expect(mocks.fetchSavingsGoal).toHaveBeenCalledTimes(2)
      })
      expect(mocks.updateSavingsGoal).toHaveBeenCalledOnce()
      expect(within(form).getByLabelText(/Goal name/)).toHaveValue(
        'Changed elsewhere',
      )
      expect(within(form).getByLabelText(/Target amount/)).toHaveValue(
        15_000_000,
      )
      expect(screen.getByRole('heading', { name: 'Edit savings goal' }))
        .toBeInTheDocument()
    },
  )

  it('loads contribution history on demand with loading, empty, error, and retry states', async () => {
    const history = deferred<SavingsEntryResource[]>()
    mocks.fetchSavingsEntries.mockReturnValueOnce(history.promise)
    await renderLoaded()

    expect(mocks.fetchSavingsEntries).not.toHaveBeenCalled()
    await openHistory()
    expect(screen.getByText('Loading savings entries…')).toBeInTheDocument()

    history.resolve([])
    expect(await screen.findByText('No contributions recorded yet.'))
      .toBeInTheDocument()

    mocks.fetchSavingsEntries.mockRejectedValueOnce(new ApiError({
      status: 500,
      code: 'server_error',
      message: 'Contribution history is unavailable.',
    }))
    await openHistory()
    expect(await screen.findByText('Contribution history is unavailable.'))
      .toBeInTheDocument()

    mocks.fetchSavingsEntries.mockResolvedValueOnce([entryFixture()])
    fireEvent.click(screen.getByRole('button', { name: 'Retry history' }))
    expect(await screen.findByText('Monthly saving')).toBeInTheDocument()
    expect(screen.getByText('1 entry')).toBeInTheDocument()
  })

  it('records a contribution with an idempotency key, blocks duplicates, and refreshes the balance', async () => {
    const updated = {
      ...goal,
      actual_balance_kyat: 8_600_000,
      remaining_kyat: 3_400_000,
    }
    mocks.fetchSavingsGoals
      .mockResolvedValueOnce([goal])
      .mockResolvedValue([updated])
    mocks.fetchSavingsEntries.mockResolvedValue([entryFixture()])
    render(<SavingsGoalsSection />)
    await screen.findByText('Rainy day fund')

    const form = openContributionForm()
    fillEntryForm(form, { amount: '200000', note: 'Monthly saving' })
    const pending = deferred<SavingsEntryResource>()
    mocks.createSavingsContribution.mockReturnValue(pending.promise)

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createSavingsContribution).toHaveBeenCalledOnce()
    })
    expect(mocks.createSavingsContribution).toHaveBeenCalledWith(
      'access-token-1',
      goal.id,
      {
        amount_kyat: 200_000,
        entry_date: '2026-11-03',
        note: 'Monthly saving',
      },
      'key-1',
    )

    pending.resolve(entryFixture())
    expect(await screen.findByText('Contribution recorded.')).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.fetchSavingsGoals).toHaveBeenCalledTimes(2)
    })
    expect(mocks.fetchSavingsEntries).toHaveBeenCalledWith(
      'access-token-1',
      goal.id,
    )
    expect(await screen.findByText('Monthly saving')).toBeInTheDocument()
    expect(within(card()).getByText('8,600,000 MMK')).toBeInTheDocument()
    expect(within(card()).getByText('3,400,000 MMK')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Add contribution' }))
      .not.toBeInTheDocument()
  })

  it('reuses a contribution idempotency key only for an unchanged retry', async () => {
    mocks.createIdempotencyKey
      .mockReturnValueOnce('key-1')
      .mockReturnValueOnce('key-2')
    const networkError = new ApiError({
      status: null,
      code: 'network_error',
      message: 'Unable to reach the FinPath API.',
    })
    mocks.createSavingsContribution
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(entryFixture())
    await renderLoaded()

    const form = openContributionForm()
    fillEntryForm(form, { amount: '2500', note: 'First note' })

    fireEvent.submit(form)
    await screen.findByText(/Unable to reach the FinPath API/)
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createSavingsContribution).toHaveBeenCalledTimes(2)
    })

    fireEvent.change(within(form).getByLabelText(/Note/), {
      target: { value: 'Changed note' },
    })
    fireEvent.submit(form)
    await waitFor(() => {
      expect(mocks.createSavingsContribution).toHaveBeenCalledTimes(3)
    })

    expect(
      mocks.createSavingsContribution.mock.calls.map((call) => call[3]),
    ).toEqual(['key-1', 'key-1', 'key-2'])
    expect(mocks.createIdempotencyKey).toHaveBeenCalledTimes(2)
    expect(await screen.findByText('Contribution recorded.')).toBeInTheDocument()
  })

  it('rejects decimal and over-limit amounts and invalid targets without a request', async () => {
    await renderLoaded()
    const form = openCreateForm()

    fillGoalForm(form, { target: '100.5' })
    fireEvent.click(within(form).getByRole('button', { name: 'Add goal' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat target/,
    )
    expect(mocks.createSavingsGoal).not.toHaveBeenCalled()

    fireEvent.change(within(form).getByLabelText(/Target amount/), {
      target: { value: '9007199254740992' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Add goal' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat target/,
    )
    expect(mocks.createSavingsGoal).not.toHaveBeenCalled()

    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    const entry = openContributionForm()
    fireEvent.change(within(entry).getByLabelText(/Amount/), {
      target: { value: '25.50' },
    })
    fireEvent.click(within(entry).getByRole('button', {
      name: 'Save contribution',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /positive whole-Kyat contribution/,
    )
    expect(mocks.createSavingsContribution).not.toHaveBeenCalled()

    fireEvent.change(within(entry).getByLabelText(/Amount/), {
      target: { value: '1000000001' },
    })
    fireEvent.click(within(entry).getByRole('button', {
      name: 'Save contribution',
    }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      /up to 1,000,000,000 MMK/,
    )
    expect(mocks.createSavingsContribution).not.toHaveBeenCalled()
  })

  it('shows backend archived-goal and validation errors without closing the form', async () => {
    await renderLoaded()
    mocks.createSavingsContribution.mockRejectedValue(new ApiError({
      status: 409,
      code: 'goal_archived',
      message: 'This savings goal is archived and cannot accept changes.',
    }))

    const form = openContributionForm()
    fillEntryForm(form, { amount: '2500' })
    fireEvent.click(within(form).getByRole('button', {
      name: 'Save contribution',
    }))

    expect(await screen.findByText(
      'This savings goal is archived and cannot accept changes.',
    )).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Add contribution' }))
      .toBeInTheDocument()
    expect(mocks.createSavingsContribution).toHaveBeenCalledOnce()
  })

  it('records a withdrawal with an idempotency key and refreshes the balance', async () => {
    const updated = {
      ...goal,
      actual_balance_kyat: 8_200_000,
      remaining_kyat: 3_800_000,
    }
    mocks.fetchSavingsGoals
      .mockResolvedValueOnce([goal])
      .mockResolvedValue([updated])
    mocks.fetchSavingsEntries.mockResolvedValue([
      entryFixture({
        id: 'entry-withdrawal',
        entry_type: 'withdrawal',
        amount_kyat: 200_000,
        note: 'Cash out',
      }),
    ])
    render(<SavingsGoalsSection />)
    await screen.findByText('Rainy day fund')

    const form = openWithdrawalForm()
    expect(within(form).getByRole('heading', {
      name: 'Withdraw from savings',
    })).toBeInTheDocument()
    expect(within(form).getByText('Available 8,400,000 MMK'))
      .toBeInTheDocument()
    fillEntryForm(form, { amount: '200000', note: 'Cash out' })
    const pending = deferred<SavingsEntryResource>()
    mocks.createSavingsWithdrawal.mockReturnValue(pending.promise)

    fireEvent.submit(form)
    fireEvent.submit(form)

    await waitFor(() => {
      expect(mocks.createSavingsWithdrawal).toHaveBeenCalledOnce()
    })
    expect(mocks.createSavingsWithdrawal).toHaveBeenCalledWith(
      'access-token-1',
      goal.id,
      {
        amount_kyat: 200_000,
        entry_date: '2026-11-03',
        note: 'Cash out',
      },
      'key-1',
    )
    expect(mocks.createSavingsContribution).not.toHaveBeenCalled()

    pending.resolve(entryFixture({
      id: 'entry-withdrawal',
      entry_type: 'withdrawal',
      amount_kyat: 200_000,
      note: 'Cash out',
    }))
    expect(await screen.findByText('Withdrawal recorded.')).toBeInTheDocument()
    await waitFor(() => {
      expect(mocks.fetchSavingsGoals).toHaveBeenCalledTimes(2)
    })
    expect(await screen.findByText('Cash out')).toBeInTheDocument()
    expect(within(card()).getByText('8,200,000 MMK')).toBeInTheDocument()
    expect(within(card()).getByText('3,800,000 MMK')).toBeInTheDocument()
    expect(screen.queryByRole('heading', {
      name: 'Withdraw from savings',
    })).not.toBeInTheDocument()
  })

  it('blocks a withdrawal above the available balance before calling the API', async () => {
    await renderLoaded()
    const form = openWithdrawalForm()
    fillEntryForm(form, { amount: '8400001' })

    fireEvent.click(within(form).getByRole('button', {
      name: 'Save withdrawal',
    }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      /Withdrawal cannot exceed the available savings balance of 8,400,000 MMK/,
    )
    expect(mocks.createSavingsWithdrawal).not.toHaveBeenCalled()
  })
})
