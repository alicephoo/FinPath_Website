import { expect, test } from '@playwright/test'
import {
  createGoal,
  createTrip,
  createUser,
  isoDate,
  loginThroughUi,
  openDetails,
  openTab,
} from './support/finpath'

test.describe('Existing feature regression', () => {
  test('creates a transaction and shows it in the live list', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'tx')
    const marker = `E2E salary ${Date.now()}`

    await loginThroughUi(page, user.credentials)
    await openTab(page, 'Transactions')

    await page
      .getByRole('button', { name: 'Add transaction', exact: true })
      .click()
    const form = page.locator('form.transaction-form')
    await expect(
      form.getByRole('heading', { name: 'Add transaction' }),
    ).toBeVisible()

    await form.getByLabel(/^Description/).fill(marker)
    await form.getByLabel(/^Type/).selectOption('income')
    const category = form.getByLabel(/^Category/)
    await expect(
      category.locator('option', { hasText: 'Salary' }),
    ).toHaveCount(1)
    await category.selectOption({ label: 'Salary' })
    await form.getByLabel(/Amount/).fill('750000')
    await form.getByLabel(/^Date/).fill(isoDate())
    await form.getByRole('button', { name: 'Add transaction' }).click()

    await expect(page.getByText('Transaction added.')).toBeVisible()
    const row = page.getByRole('row', { name: new RegExp(marker) })
    await expect(row).toBeVisible()
    await expect(
      row.getByText('750,000 MMK', { exact: true }),
    ).toBeVisible()
    await expect(row.getByText('income', { exact: true })).toBeVisible()
    await expect(row.getByText('Salary', { exact: true })).toBeVisible()
  })

  test('adds a loan and records a repayment', async ({ page, request }) => {
    const user = await createUser(request, 'loan')
    const loanName = `E2E car loan ${Date.now()}`

    await loginThroughUi(page, user.credentials)
    await openTab(page, 'Loans')

    await page.getByRole('button', { name: 'Add loan', exact: true }).click()
    const loanForm = page.locator('form.loan-form')
    await loanForm.getByLabel(/^Loan name/).fill(loanName)
    await loanForm.getByLabel(/Original principal/).fill('1000000')
    await loanForm.getByRole('button', { name: 'Add loan' }).click()

    await expect(page.getByText('Loan added.')).toBeVisible()
    const card = page.locator('article.loan-record', {
      has: page.getByRole('heading', { name: loanName }),
    })
    await expect(card).toBeVisible()
    await expect(card.locator('.outstanding-number strong')).toHaveText(
      '1,000,000 MMK',
    )

    await card.getByRole('button', { name: 'Record repayment' }).click()
    const repaymentForm = card.locator('form.repayment-form')
    await expect(
      repaymentForm.getByRole('heading', { name: 'Record repayment' }),
    ).toBeVisible()
    await repaymentForm.getByLabel(/Amount/).fill('250000')
    await repaymentForm.getByLabel(/^Note/).fill('First repayment')
    await repaymentForm
      .getByRole('button', { name: 'Save repayment' })
      .click()

    await expect(page.getByText('Repayment recorded.')).toBeVisible()
    await expect(card.locator('.outstanding-number strong')).toHaveText(
      '750,000 MMK',
    )
    const history = card.locator('details.repayment-history')
    await openDetails(history)
    await expect(history.getByText('First repayment')).toBeVisible()
    await expect(
      history.getByText('250,000 MMK', { exact: true }),
    ).toBeVisible()
  })

  test('records a savings contribution', async ({ page, request }) => {
    const user = await createUser(request, 'contribution')
    const goalName = `E2E goal ${Date.now()}`
    const goal = await createGoal(request, user.accessToken, {
      name: goalName,
      targetKyat: 100_000,
    })

    await loginThroughUi(page, user.credentials)
    await openTab(page, 'Savings Goals')

    const card = page.locator('article.savings-goal-card', {
      has: page.getByRole('heading', { name: goalName }),
    })
    await expect(card).toBeVisible()
    await card.getByRole('button', { name: 'Add contribution' }).click()
    const form = card.locator('form.savings-entry-form')
    await expect(
      form.getByRole('heading', { name: 'Add contribution' }),
    ).toBeVisible()
    await form.getByLabel(/Amount/).fill('25000')
    await form.getByLabel(/^Note/).fill('Monthly saving')
    await form.getByRole('button', { name: 'Save contribution' }).click()

    await expect(page.getByText('Contribution recorded.')).toBeVisible()
    await expect(
      card.getByText('25,000 MMK', { exact: true }),
    ).toBeVisible()
    await expect(card.getByText('75,000 MMK', { exact: true })).toBeVisible()
    const history = card.locator('details.savings-history')
    await openDetails(history)
    await expect(history.getByText('Monthly saving')).toBeVisible()
    await expect(history.getByText(/Contribution ·/)).toBeVisible()
    expect(goal.id).toBeTruthy()
  })

  test('adds a travel budget and records actual spending', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'travel')
    const tripName = `E2E trip ${Date.now()}`
    await createTrip(request, user.accessToken, {
      name: tripName,
      destination: 'Rome, Italy',
    })

    await loginThroughUi(page, user.credentials)
    await openTab(page, 'Travel Budget')

    const tripCard = page.locator('article.trip-record', {
      has: page.getByRole('heading', { name: tripName }),
    })
    await expect(tripCard).toBeVisible()

    await tripCard
      .getByRole('button', { name: 'Add category budget' })
      .click()
    const budgetForm = tripCard.locator('form.trip-budget-form')
    await budgetForm.getByLabel(/^Category/).fill('Lodging')
    await budgetForm.getByLabel(/Budget amount/).fill('400000')
    await budgetForm.getByRole('button', { name: 'Add budget' }).click()
    await expect(page.getByText('Planned expense added.')).toBeVisible()
    await expect(
      tripCard.getByRole('heading', { name: 'Lodging' }),
    ).toBeVisible()

    await tripCard
      .getByRole('button', { name: 'Record actual spending' })
      .click()
    const spendingForm = tripCard.locator('form.trip-spending-form')
    await spendingForm.getByLabel(/^Description/).fill('Hotel deposit')
    await spendingForm.getByLabel(/Amount/).fill('150000')
    await spendingForm.getByLabel(/^Date/).fill(isoDate(8))
    await spendingForm.getByRole('button', { name: 'Save spending' }).click()

    await expect(page.getByText('Spending recorded.')).toBeVisible()
    const history = tripCard.locator('details.trip-spending-history')
    await openDetails(history)
    await expect(history.getByText('Hotel deposit')).toBeVisible()
    await expect(
      tripCard.getByText('150,000 MMK', { exact: true }).first(),
    ).toBeVisible()
    await expect(
      tripCard.getByText('250,000 MMK', { exact: true }).first(),
    ).toBeVisible()
  })

  test('sets the starting balance and shows the calculated balance', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'balance')

    await loginThroughUi(page, user.credentials)
    await expect(
      page.getByRole('heading', { name: 'Set up starting balance' }),
    ).toBeVisible()

    const form = page.locator('form.account-balance-form')
    await form.getByLabel(/Starting bank balance/).fill('5000000')
    await form.getByLabel(/Effective date/).fill(isoDate())
    await form.getByRole('button', { name: 'Set starting balance' }).click()

    const balanceSection = page.locator(
      'section[aria-labelledby="account-balance-title"]',
    )
    await expect(balanceSection.locator('.account-balance-value')).toHaveText(
      '5,000,000 MMK',
    )
    await expect(
      balanceSection.getByRole('button', { name: 'Edit starting balance' }),
    ).toBeVisible()
  })
})
