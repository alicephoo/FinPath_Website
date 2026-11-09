import { getOutstandingPrincipalKyat, type Loan } from './loansMockData'
import { getSavingsBalanceKyat, getSavingsProgress, type SavingsGoal } from './savingsGoalsMockData'
import {
  getTripBudgetKyat,
  getTripSpendingKyat,
  type Trip,
} from './travelBudgetMockData'
import type { Transaction } from './transactionsMockData'
import { calculateAccountBalanceKyat, type StartingBalance } from './accountBalance'
import { formatMoney, moneyToEntryString, sumMoneyKyat } from './money'

export type DashboardMonthReport = {
  key: string
  label: string
  incomeKyat: number
  expenseKyat: number
}

export type DashboardCategoryReport = {
  category: string
  expenseKyat: number
}

export type DashboardReportRow = {
  section: string
  record: string
  dateOrPeriod: string
  category: string
  amountKyat: number | ''
  comparisonKyat: number | ''
  statusOrNotes: string
}

export type DashboardReport = {
  accountBalanceKyat: number | null
  allTimeIncomeKyat: number
  allTimeExpenseKyat: number
  currentMonthIncomeKyat: number
  currentMonthExpenseKyat: number
  outstandingPrincipalKyat: number
  principalRepaidKyat: number
  savingsBalanceKyat: number
  savingsTargetKyat: number
  travelBudgetKyat: number
  travelSpendingKyat: number
  months: DashboardMonthReport[]
  expenseCategories: DashboardCategoryReport[]
  rows: DashboardReportRow[]
}

function localMonthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

function monthLabel(monthKey: string): string {
  const [year, month] = monthKey.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric' })
    .format(new Date(year, month - 1, 1))
}

export function buildDashboardReport(
  transactions: Transaction[],
  loans: Loan[],
  savingsGoals: SavingsGoal[],
  trips: Trip[],
  now = new Date(),
  startingBalance: StartingBalance | null = null,
): DashboardReport {
  const currentMonthKey = localMonthKey(now)
  const months = Array.from({ length: 6 }, (_, index) => {
    const date = new Date(now.getFullYear(), now.getMonth() - 5 + index, 1)
    const key = localMonthKey(date)
    return { key, label: monthLabel(key), incomeKyat: 0, expenseKyat: 0 }
  })
  const monthByKey = new Map(months.map((month) => [month.key, month]))
  const categoryTotals = new Map<string, number>()
  for (const transaction of transactions) {
    const amountKyat = transaction.amountKyat
    const monthKey = transaction.date.slice(0, 7)
    const month = monthByKey.get(monthKey)
    if (transaction.type === 'income') {
      if (month) month.incomeKyat = sumMoneyKyat([month.incomeKyat, amountKyat])
    } else {
      if (month) month.expenseKyat = sumMoneyKyat([month.expenseKyat, amountKyat])
      categoryTotals.set(transaction.category, sumMoneyKyat([categoryTotals.get(transaction.category) ?? 0, amountKyat]))
    }
  }

  const allTimeIncomeKyat = sumMoneyKyat(transactions
    .filter((transaction) => transaction.type === 'income')
    .map((transaction) => transaction.amountKyat))
  const allTimeExpenseKyat = sumMoneyKyat(transactions
    .filter((transaction) => transaction.type === 'expense')
    .map((transaction) => transaction.amountKyat))
  const currentMonthTransactions = transactions.filter((transaction) => transaction.date.slice(0, 7) === currentMonthKey)
  const currentMonthIncomeKyat = sumMoneyKyat(currentMonthTransactions
    .filter((transaction) => transaction.type === 'income')
    .map((transaction) => transaction.amountKyat))
  const currentMonthExpenseKyat = sumMoneyKyat(currentMonthTransactions
    .filter((transaction) => transaction.type === 'expense')
    .map((transaction) => transaction.amountKyat))

  const expenseCategories = Array.from(categoryTotals, ([category, expenseKyat]) => ({ category, expenseKyat }))
    .sort((left, right) => right.expenseKyat - left.expenseKyat || left.category.localeCompare(right.category))
  const outstandingPrincipalKyat = sumMoneyKyat(loans.map((loan) => getOutstandingPrincipalKyat(loan)))
  const principalRepaidKyat = sumMoneyKyat(loans.flatMap((loan) => loan.repayments.map((repayment) => repayment.amountKyat)))
  const savingsBalanceKyat = sumMoneyKyat(savingsGoals.map((goal) => getSavingsBalanceKyat(goal)))
  const savingsTargetKyat = sumMoneyKyat(savingsGoals.map((goal) => goal.targetKyat))
  const travelBudgetKyat = sumMoneyKyat(trips.map((trip) => getTripBudgetKyat(trip)))
  const travelSpendingKyat = sumMoneyKyat(trips.map((trip) => getTripSpendingKyat(trip)))
  const accountBalanceKyat = startingBalance
    ? calculateAccountBalanceKyat(startingBalance, transactions)
    : null

  const rows: DashboardReportRow[] = [
    { section: 'Summary', record: 'Calculated account balance', dateOrPeriod: startingBalance ? `As of ${localDateKey(now)}` : 'Not set', category: '', amountKyat: accountBalanceKyat ?? '', comparisonKyat: '', statusOrNotes: startingBalance ? `Starting balance ${formatMoney(startingBalance.amountKyat)} effective at end of ${startingBalance.effectiveDate}; only later transactions applied` : 'Manually enter a starting balance and effective date' },
    { section: 'Summary', record: 'All-time recorded income', dateOrPeriod: 'All time', category: '', amountKyat: allTimeIncomeKyat, comparisonKyat: '', statusOrNotes: 'Transactions only' },
    { section: 'Summary', record: 'All-time recorded transaction expenses', dateOrPeriod: 'All time', category: '', amountKyat: allTimeExpenseKyat, comparisonKyat: '', statusOrNotes: 'Travel spending reported separately' },
    { section: 'Summary', record: 'Net transaction flow', dateOrPeriod: 'All time', category: '', amountKyat: sumMoneyKyat([allTimeIncomeKyat, -allTimeExpenseKyat]), comparisonKyat: '', statusOrNotes: 'Recorded income minus recorded transaction expenses; not an account balance' },
    { section: 'Summary', record: 'Current-month income', dateOrPeriod: currentMonthKey, category: '', amountKyat: currentMonthIncomeKyat, comparisonKyat: '', statusOrNotes: 'Transactions only' },
    { section: 'Summary', record: 'Current-month transaction expenses', dateOrPeriod: currentMonthKey, category: '', amountKyat: currentMonthExpenseKyat, comparisonKyat: '', statusOrNotes: 'Travel spending reported separately' },
    { section: 'Summary', record: 'Outstanding loan principal', dateOrPeriod: 'Current', category: '', amountKyat: outstandingPrincipalKyat, comparisonKyat: '', statusOrNotes: 'Liability; original principal is not an expense' },
    { section: 'Summary', record: 'Recorded principal repayments', dateOrPeriod: 'All time', category: '', amountKyat: principalRepaidKyat, comparisonKyat: '', statusOrNotes: 'Shown separately; not included in transaction expenses' },
    { section: 'Summary', record: 'Net savings balance', dateOrPeriod: 'Current', category: '', amountKyat: savingsBalanceKyat, comparisonKyat: savingsTargetKyat, statusOrNotes: 'Recorded contributions less withdrawals; excludes planned savings' },
    { section: 'Summary', record: 'Travel budget total', dateOrPeriod: 'Current plans', category: '', amountKyat: travelBudgetKyat, comparisonKyat: '', statusOrNotes: 'Planned budget; not an expense' },
    { section: 'Summary', record: 'Trip-owned actual spending', dateOrPeriod: 'Recorded entries', category: '', amountKyat: travelSpendingKyat, comparisonKyat: travelBudgetKyat, statusOrNotes: 'Separate from transactions; not added to expenses' },
  ]
  if (startingBalance) {
    rows.push({
      section: 'Starting balance',
      record: 'Manually entered account balance',
      dateOrPeriod: startingBalance.effectiveDate,
      category: 'Effective at end of date',
      amountKyat: startingBalance.amountKyat,
      comparisonKyat: '',
      statusOrNotes: 'Manual input; no bank connection',
    })
  }

  for (const month of months) {
    rows.push({
      section: 'Cash flow',
      record: 'Recorded transactions',
      dateOrPeriod: month.key,
      category: '',
      amountKyat: month.incomeKyat,
      comparisonKyat: month.expenseKyat,
      statusOrNotes: 'Income in Amount; transaction expenses in Comparison',
    })
  }

  for (const category of expenseCategories) {
    rows.push({ section: 'Expense category', record: category.category, dateOrPeriod: 'All time', category: category.category, amountKyat: category.expenseKyat, comparisonKyat: '', statusOrNotes: 'Transaction expenses only' })
  }
  for (const transaction of transactions) {
    rows.push({
      section: 'Transaction',
      record: transaction.description,
      dateOrPeriod: transaction.date,
      category: transaction.category,
      amountKyat: transaction.amountKyat,
      comparisonKyat: '',
      statusOrNotes: transaction.type,
    })
  }
  for (const loan of loans) {
    const repaidKyat = sumMoneyKyat(loan.repayments.map((repayment) => repayment.amountKyat))
    rows.push({
      section: 'Loan',
      record: loan.name,
      dateOrPeriod: loan.startDate,
      category: 'Outstanding principal',
      amountKyat: getOutstandingPrincipalKyat(loan),
      comparisonKyat: repaidKyat,
      statusOrNotes: 'Amount is outstanding principal; Comparison is recorded repayments, not expenses',
    })
    for (const repayment of loan.repayments) {
      rows.push({
        section: 'Loan repayment',
        record: `${loan.name}${repayment.note ? ` · ${repayment.note}` : ''}`,
        dateOrPeriod: repayment.date,
        category: 'Principal repayment',
        amountKyat: repayment.amountKyat,
        comparisonKyat: '',
        statusOrNotes: 'Separate from transaction expenses',
      })
    }
  }
  for (const goal of savingsGoals) {
    const balanceKyat = getSavingsBalanceKyat(goal)
    rows.push({
      section: 'Savings goal',
      record: goal.name,
      dateOrPeriod: 'Current',
      category: 'Actual balance',
      amountKyat: balanceKyat,
      comparisonKyat: goal.targetKyat,
      statusOrNotes: `${getSavingsProgress(goal)}% of target; planned and missed amounts excluded`,
    })
    for (const entry of goal.entries) {
      rows.push({
        section: 'Savings entry',
        record: `${goal.name}${entry.note ? ` · ${entry.note}` : ''}`,
        dateOrPeriod: entry.date,
        category: entry.type,
        amountKyat: entry.amountKyat,
        comparisonKyat: '',
        statusOrNotes: 'Recorded savings activity',
      })
    }
  }
  for (const trip of trips) {
    rows.push({
      section: 'Travel budget',
      record: trip.name,
      dateOrPeriod: `${trip.startDate} to ${trip.endDate}`,
      category: trip.destination,
      amountKyat: getTripBudgetKyat(trip),
      comparisonKyat: getTripSpendingKyat(trip),
      statusOrNotes: 'Budget in Amount; trip-owned spending in Comparison; separate from expenses',
    })
    for (const spending of trip.spending) {
      rows.push({
        section: 'Trip spending',
        record: `${trip.name} · ${spending.description}`,
        dateOrPeriod: spending.date,
        category: spending.category,
        amountKyat: spending.amountKyat,
        comparisonKyat: '',
        statusOrNotes: 'Trip-owned actual spending; not linked to transaction records',
      })
    }
  }

  return {
    accountBalanceKyat,
    allTimeIncomeKyat,
    allTimeExpenseKyat,
    currentMonthIncomeKyat,
    currentMonthExpenseKyat,
    outstandingPrincipalKyat,
    principalRepaidKyat,
    savingsBalanceKyat,
    savingsTargetKyat,
    travelBudgetKyat,
    travelSpendingKyat,
    months,
    expenseCategories,
    rows,
  }

  function localDateKey(date: Date): string {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  }
}

function escapeCsvCell(value: string | number, protectFormula = false): string {
  let text = String(value)
  if (protectFormula && /^[\s\u0000-\u001f\u007f-\u009f]*[=+\-@]/u.test(text)) {
    text = `'${text}`
  }
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function dashboardReportToCsv(report: DashboardReport): string {
  const header = ['Section', 'Record', 'Date or period', 'Category', 'Amount (MMK)', 'Comparison (MMK)', 'Status or notes']
  const csvRows = [
    header.map((cell) => escapeCsvCell(cell)),
    ...report.rows.map((row) => [
      escapeCsvCell(row.section, true),
      escapeCsvCell(row.record, true),
      escapeCsvCell(row.dateOrPeriod, true),
      escapeCsvCell(row.category, true),
      escapeCsvCell(row.amountKyat === '' ? '' : moneyToEntryString(row.amountKyat)),
      escapeCsvCell(row.comparisonKyat === '' ? '' : moneyToEntryString(row.comparisonKyat)),
      escapeCsvCell(row.statusOrNotes, true),
    ]),
  ]
  return csvRows.map((row) => row.join(',')).join('\r\n')
}
