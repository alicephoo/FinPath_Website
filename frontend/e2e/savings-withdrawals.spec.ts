import { expect, test } from '@playwright/test'
import {
  createContribution,
  createGoal,
  createUser,
  loginThroughUi,
  openDetails,
  openTab,
} from './support/finpath'

test.describe('Savings goals and withdrawals', () => {
  test('shows history, records a withdrawal, and rejects overdraw and invalid amounts', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'savings')
    const goalName = `Emergency fund ${Date.now()}`

    const goal = await createGoal(request, user.accessToken, {
      name: goalName,
      targetKyat: 500_000,
    })
    await createContribution(request, user.accessToken, goal.id, {
      amountKyat: 100_000,
      note: 'Initial deposit',
    })

    await loginThroughUi(page, user.credentials)
    await openTab(page, 'Savings Goals')

    const card = page.locator('article.savings-goal-card', {
      has: page.getByRole('heading', { name: goalName }),
    })
    await expect(card).toBeVisible()
    await expect(
      card.getByText('100,000 MMK', { exact: true }),
    ).toBeVisible()

    // History loads on demand and shows the seeded contribution.
    const history = card.locator('details.savings-history')
    await openDetails(history)
    await expect(history.getByText('Initial deposit')).toBeVisible()
    await expect(history.getByText(/Contribution ·/)).toBeVisible()

    // Valid withdrawal: balance and history update from authoritative data.
    await card.getByRole('button', { name: 'Withdraw' }).click()
    const withdrawalForm = card.locator('form.savings-entry-form')
    await expect(
      withdrawalForm.getByRole('heading', {
        name: 'Withdraw from savings',
      }),
    ).toBeVisible()
    await withdrawalForm.getByLabel(/Amount/).fill('40000')
    await withdrawalForm.getByLabel(/Note/).fill('Cash out')
    await withdrawalForm
      .getByRole('button', { name: 'Save withdrawal' })
      .click()

    await expect(page.getByText('Withdrawal recorded.')).toBeVisible()
    await expect(
      card.getByText('60,000 MMK', { exact: true }),
    ).toBeVisible()

    // Opening the entry form remounts the uncontrolled history panel, so
    // reopen it before checking the new withdrawal entry.
    await openDetails(history)
    await expect(history.getByText('Cash out')).toBeVisible()
    await expect(history.getByText(/Withdrawal ·/)).toBeVisible()
    await expect(history.getByText(/40,000 MMK/)).toBeVisible()

    // Overdraw is blocked before any API call.
    await card.getByRole('button', { name: 'Withdraw' }).click()
    const overdrawForm = card.locator('form.savings-entry-form')
    await overdrawForm.getByLabel(/Amount/).fill('1000000')
    await overdrawForm
      .getByRole('button', { name: 'Save withdrawal' })
      .click()
    await expect(card.getByRole('alert')).toContainText(
      /Withdrawal cannot exceed the available savings balance of 60,000 MMK/,
    )

    // Zero and decimal amounts are also rejected client-side.
    await overdrawForm.getByLabel(/Amount/).fill('0')
    await overdrawForm
      .getByRole('button', { name: 'Save withdrawal' })
      .click()
    await expect(card.getByRole('alert')).toContainText(
      /Enter a positive whole-Kyat withdrawal/,
    )

    await overdrawForm.getByLabel(/Amount/).fill('1.5')
    await overdrawForm
      .getByRole('button', { name: 'Save withdrawal' })
      .click()
    await expect(card.getByRole('alert')).toContainText(
      /Enter a positive whole-Kyat withdrawal/,
    )
  })
})
