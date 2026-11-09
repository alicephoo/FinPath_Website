import { describe, expect, it } from 'vitest'
import { buildDashboardReport, dashboardReportToCsv } from './dashboardReports'
import type { Loan } from './loansMockData'
import type { SavingsGoal } from './savingsGoalsMockData'
import type { Transaction } from './transactionsMockData'
import type { Trip } from './travelBudgetMockData'

const now = new Date(2026, 9, 30, 12)

const transactions: Transaction[] = [
  { id: 'income', description: 'October salary', type: 'income', category: 'Salary', amountKyat: 100_000, date: '2026-10-20' },
  { id: 'expense', description: 'Food, groceries', type: 'expense', category: 'Food', amountKyat: 2_500, date: '2026-10-21' },
]

const loans: Loan[] = [{
  id: 'loan',
  name: 'Car loan',
  principalKyat: 50_000,
  startDate: '2026-01-01',
  repayments: [{ id: 'repayment', amountKyat: 5_000, date: '2026-10-22', note: 'Monthly payment' }],
}]

const savingsGoals: SavingsGoal[] = [{
  id: 'goal',
  name: 'Emergency fund',
  targetKyat: 20_000,
  tone: 'mint',
  entries: [
    { id: 'contribution', type: 'contribution', amountKyat: 6_000, date: '2026-10-20', note: 'Payday' },
    { id: 'withdrawal', type: 'withdrawal', amountKyat: 1_000, date: '2026-10-21', note: 'Repair' },
  ],
  dailyPlan: {
    startDate: '2026-10-30',
    durationDays: 1,
    dailyAmountKyat: 99_000,
    days: [],
  },
}]

const trips: Trip[] = [{
  id: 'trip',
  name: 'Lisbon',
  destination: 'Portugal',
  startDate: '2026-11-01',
  endDate: '2026-11-03',
  status: 'active',
  budgets: [{ id: 'budget', category: 'Flights', budgetKyat: 30_000 }],
  spending: [{ id: 'spending', categoryBudgetId: 'budget', category: 'Flights', description: 'Ticket', amountKyat: 4_000, date: '2026-10-20' }],
}]

describe('Dashboard report calculations', () => {
  it('separates transaction, repayment, travel, and planned savings totals', () => {
    const report = buildDashboardReport(transactions, loans, savingsGoals, trips, now)
    expect(report.allTimeIncomeKyat).toBe(100_000)
    expect(report.allTimeExpenseKyat).toBe(2_500)
    expect(report.currentMonthIncomeKyat).toBe(100_000)
    expect(report.currentMonthExpenseKyat).toBe(2_500)
    expect(report.principalRepaidKyat).toBe(5_000)
    expect(report.outstandingPrincipalKyat).toBe(45_000)
    expect(report.savingsBalanceKyat).toBe(5_000)
    expect(report.savingsTargetKyat).toBe(20_000)
    expect(report.travelBudgetKyat).toBe(30_000)
    expect(report.travelSpendingKyat).toBe(4_000)
  })

  it('includes a calculated whole-Kyat account balance in report values and rows', () => {
    const report = buildDashboardReport(
      transactions,
      loans,
      savingsGoals,
      trips,
      now,
      { amountKyat: 1_000, effectiveDate: '2026-10-19' },
    )
    expect(report.accountBalanceKyat).toBe(98_500)
    expect(report.rows.find((row) => row.record === 'Calculated account balance')?.amountKyat).toBe(98_500)
    expect(report.rows.find((row) => row.record === 'Calculated account balance')?.statusOrNotes)
      .toContain('Starting balance 1,000 MMK')
  })

  it('keeps loan repayments and trip spending outside transaction expense rows', () => {
    const report = buildDashboardReport(transactions, loans, savingsGoals, trips, now)
    expect(report.allTimeExpenseKyat).toBe(2_500)
    expect(report.rows.find((row) => row.section === 'Loan repayment')?.amountKyat).toBe(5_000)
    expect(report.rows.find((row) => row.section === 'Trip spending')?.amountKyat).toBe(4_000)
    expect(report.expenseCategories).toEqual([{ category: 'Food', expenseKyat: 2_500 }])
  })

  it('throws instead of reporting unsafe aggregate totals', () => {
    expect(() => buildDashboardReport(
      [
        { ...transactions[0], amountKyat: Number.MAX_SAFE_INTEGER },
        { ...transactions[0], id: 'overflow', amountKyat: 1 },
      ],
      [],
      [],
      [],
      now,
    )).toThrow(RangeError)
  })
})

describe('Dashboard CSV', () => {
  it('uses MMK headers and escapes commas, quotes, and embedded line breaks', () => {
    const report = buildDashboardReport(transactions, [], [], [], now)
    const csv = dashboardReportToCsv(report)
    expect(csv.startsWith('Section,Record,Date or period,Category,Amount (MMK),Comparison (MMK),Status or notes\r\n')).toBe(true)
    expect(csv).toContain('"Food, groceries"')
    const customReport = {
      ...report,
      rows: [{
        section: 'Transaction',
        record: 'A "quoted", description\r\ncontinued',
        dateOrPeriod: '2026-10-30',
        category: 'Food',
        amountKyat: 100,
        comparisonKyat: '' as const,
        statusOrNotes: 'Normal',
      }],
    }
    expect(dashboardReportToCsv(customReport)).toContain('"A ""quoted"", description\r\ncontinued"')
  })

  it('neutralizes formula-like untrusted text, including leading control characters', () => {
    const report = buildDashboardReport(
      [{ ...transactions[0], description: '\t=SUM(1,1)', category: '\u0000@cmd' }],
      [],
      [],
      [],
      now,
    )
    const csv = dashboardReportToCsv(report)
    expect(csv).toContain('"\u0027\t=SUM(1,1)"')
    expect(csv).toContain("\u0027\u0000@cmd")
  })

  it('preserves negative whole-Kyat values and MMK notes without decimal cents', () => {
    const report = buildDashboardReport(
      [{ ...transactions[0], amountKyat: 100, type: 'expense', description: 'Rent', category: 'Housing' }],
      [],
      [],
      [],
      now,
      { amountKyat: -50, effectiveDate: '2026-10-30' },
    )
    const csv = dashboardReportToCsv(report)
    expect(csv).toContain('Calculated account balance,As of 2026-10-30,,-50,,Starting balance -50 MMK')
    expect(csv).toContain('Transaction,Rent,2026-10-20,Housing,100,,expense')
    expect(csv).not.toContain('.00')
    expect(csv).not.toContain('USD')
    expect(csv).toContain('Transactions only')
  })

  it('exports empty feature data with headers and integer zero-valued summary rows', () => {
    const csv = dashboardReportToCsv(buildDashboardReport([], [], [], [], now))
    expect(csv.startsWith('Section,Record,Date or period,Category,Amount (MMK),Comparison (MMK),Status or notes\r\n')).toBe(true)
    expect(csv).toContain('Summary,All-time recorded income,All time,,0,,Transactions only')
  })
})
