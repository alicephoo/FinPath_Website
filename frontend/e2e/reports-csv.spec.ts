import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import {
  createUser,
  isoDate,
  loginThroughUi,
  seedTransaction,
} from './support/finpath'

const CSV_HEADER =
  'Section,Record,Date or period,Category,Amount (MMK),'
  + 'Comparison (MMK),Status or notes'

test.describe('Dashboard reports and CSV export', () => {
  test('shows six-month cash flow, spending by category, and a valid CSV download', async ({
    page,
    request,
  }) => {
    const user = await createUser(request, 'reports')
    const marker = `E2E groceries ${Date.now()}`

    await seedTransaction(request, user.accessToken, {
      description: `E2E salary ${Date.now()}`,
      type: 'income',
      category: 'Salary',
      amountKyat: 500_000,
      date: isoDate(-20),
    })
    await seedTransaction(request, user.accessToken, {
      description: marker,
      type: 'expense',
      category: 'Groceries',
      amountKyat: 125_000,
      date: isoDate(-5),
    })

    await loginThroughUi(page, user.credentials)

    const cashFlow = page.locator(
      'section[aria-labelledby="report-cashflow-title"]',
    )
    await expect(
      cashFlow.getByRole('img', {
        name: 'Monthly income and expense bars for the last six months',
      }),
    ).toBeVisible()
    await expect(cashFlow.getByText(/Income/).first()).toBeVisible()
    await expect(cashFlow.getByText(/Expenses/).first()).toBeVisible()

    const categories = page.locator(
      'section[aria-labelledby="report-categories-title"]',
    )
    await expect(
      categories.getByText('Groceries', { exact: true }),
    ).toBeVisible()
    await expect(
      categories.getByText('125,000 MMK', { exact: true }),
    ).toBeVisible()

    // Trigger the browser download rather than calling the API directly.
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Export dashboard CSV' }).click()
    const download = await downloadPromise

    expect(download.suggestedFilename()).toBe(
      'finpath-dashboard-report.csv',
    )
    const downloadPath = await download.path()
    expect(downloadPath).not.toBeNull()

    const csv = readFileSync(downloadPath!, 'utf8').replace(/^\uFEFF/, '')
    const lines = csv.split('\r\n')
    expect(lines[0]).toBe(CSV_HEADER)
    expect(csv).toContain(marker)
    expect(csv).toContain('Groceries')
    expect(csv).toContain('125000')
    await expect(page.getByText('Dashboard CSV downloaded.')).toBeVisible()
  })

  test('handles empty report data gracefully', async ({ page, request }) => {
    const user = await createUser(request, 'empty-reports')
    await loginThroughUi(page, user.credentials)

    const cashFlow = page.locator(
      'section[aria-labelledby="report-cashflow-title"]',
    )
    await expect(
      cashFlow.getByText(
        'No transactions recorded in this reporting period.',
      ),
    ).toBeVisible()

    const categories = page.locator(
      'section[aria-labelledby="report-categories-title"]',
    )
    await expect(
      categories.getByText(
        'No expense transactions recorded in this reporting period.',
      ),
    ).toBeVisible()

    // The dashboard itself shows an unconfigured starting balance rather
    // than treating a missing setting as zero.
    await expect(page.getByText('Not configured')).toBeVisible()
  })
})
