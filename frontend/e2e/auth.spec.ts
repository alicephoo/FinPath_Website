import { expect, test } from '@playwright/test'
import { createUser, loginThroughUi } from './support/finpath'

test.describe('Authentication', () => {
  test('signs in, restores the session after refresh, and logs out', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'auth')

    await loginThroughUi(page, user.credentials)
    await expect(
      page.getByRole('heading', { name: 'Your overview' }),
    ).toBeVisible()
    await expect(page.getByText(user.email, { exact: true })).toBeVisible()
    await expect(
      page.getByRole('button', { name: 'Log out' }),
    ).toBeVisible()

    // Reload: the HttpOnly refresh cookie must restore the authenticated UI.
    await page.reload()
    await expect(
      page.getByRole('heading', { name: 'Your overview' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { name: 'Sign in to FinPath' }),
    ).toHaveCount(0)
    await expect(page.getByText(user.email, { exact: true })).toBeVisible()

    // Log out clears the server session and the authenticated UI.
    await page.getByRole('button', { name: 'Log out' }).click()
    await expect(
      page.getByRole('heading', { name: 'Sign in to FinPath' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { name: 'Your overview' }),
    ).toHaveCount(0)

    // Protected content stays inaccessible after another reload.
    await page.reload()
    await expect(
      page.getByRole('heading', { name: 'Sign in to FinPath' }),
    ).toBeVisible()
    await expect(
      page.getByRole('heading', { name: 'Your overview' }),
    ).toHaveCount(0)
    await expect(page.getByText(user.email, { exact: true })).toHaveCount(0)
  })
})
