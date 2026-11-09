import { expect, test } from '@playwright/test'
import {
  API_URL,
  ORIGIN,
  attemptWithdrawal,
  createContribution,
  createGoal,
  createUser,
  fetchGoal,
  isoDate,
  loginThroughUi,
  openTab,
  seedTransaction,
} from './support/finpath'

function bearer(accessToken: string): Record<string, string> {
  return { Authorization: `Bearer ${accessToken}`, Origin: ORIGIN }
}

test.describe('User isolation', () => {
  test('one user cannot see or access another user records', async ({
    page,
    request,
  }) => {
    const owner = await createUser(request, 'owner')
    const intruder = await createUser(request, 'intruder')
    const goalName = `Owner goal ${Date.now()}`
    const txMarker = `Owner transaction ${Date.now()}`

    const goal = await createGoal(request, owner.accessToken, {
      name: goalName,
      targetKyat: 200_000,
    })
    await createContribution(request, owner.accessToken, goal.id, {
      amountKyat: 50_000,
      note: 'Owner deposit',
    })
    await seedTransaction(request, owner.accessToken, {
      description: txMarker,
      type: 'expense',
      category: 'Groceries',
      amountKyat: 15_000,
      date: isoDate(),
    })

    // The second user only ever sees their own records in the UI.
    await loginThroughUi(page, intruder.credentials)
    await openTab(page, 'Savings Goals')
    await expect(page.getByText('No savings goals yet')).toBeVisible()
    await expect(page.getByText(goalName)).toHaveCount(0)

    await openTab(page, 'Transactions')
    await expect(page.getByText('No matching transactions')).toBeVisible()
    await expect(page.getByText(txMarker)).toHaveCount(0)

    // Direct API access to the owner's goal is denied as not found.
    const goalRead = await fetchGoal(request, intruder.accessToken, goal.id)
    expect(goalRead.status).toBe(404)

    const entries = await request.get(
      `${API_URL}/savings/goals/${goal.id}/entries`,
      { headers: bearer(intruder.accessToken) },
    )
    expect(entries.status()).toBe(404)

    const withdrawal = await attemptWithdrawal(
      request,
      intruder.accessToken,
      goal.id,
      10_000,
    )
    expect(withdrawal.status).toBe(404)

    const ownedTransactions = await request.get(`${API_URL}/transactions`, {
      headers: bearer(intruder.accessToken),
    })
    expect(ownedTransactions.status()).toBe(200)
    const transactionsPayload = await ownedTransactions.json()
    const descriptions = (transactionsPayload.data as Array<{
      description: string
    }>).map((item) => item.description)
    expect(descriptions).not.toContain(txMarker)

    // The owner's records are unchanged and still readable by the owner.
    const ownerRead = await fetchGoal(request, owner.accessToken, goal.id)
    expect(ownerRead.status).toBe(200)
    const ownerGoal = ownerRead.body.data as {
      name: string
      actual_balance_kyat: number
    }
    expect(ownerGoal.name).toBe(goalName)
    expect(ownerGoal.actual_balance_kyat).toBe(50_000)
  })
})
