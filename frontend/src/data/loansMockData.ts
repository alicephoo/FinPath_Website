import { formatDateOnly } from './dateOnly'
import { formatMoney, sumMoneyKyat } from './money'

export type LoanRepayment = {
  id: string
  amountKyat: number
  date: string
  note: string
}

export type Loan = {
  id: string
  name: string
  principalKyat: number
  startDate: string
  repayments: LoanRepayment[]
}

// Sample amounts are whole Myanmar Kyat (MMK); they are not USD-cent values.
export const sampleLoans: Loan[] = [
  {
    id: 'loan-home',
    name: 'Home loan',
    principalKyat: 180_000_000,
    startDate: '2022-05-12',
    repayments: [
      { id: 'repay-home-1', amountKyat: 12_000_000, date: '2026-10-01', note: 'Monthly payment' },
      { id: 'repay-home-2', amountKyat: 5_750_000, date: '2026-09-01', note: 'Monthly payment' },
    ],
  },
  {
    id: 'loan-education',
    name: 'Education loan',
    principalKyat: 18_000_000,
    startDate: '2023-08-20',
    repayments: [
      { id: 'repay-education-1', amountKyat: 4_500_000, date: '2026-10-12', note: 'Monthly payment' },
    ],
  },
]

export function getOutstandingPrincipalKyat(loan: Loan): number {
  return Math.max(0, loan.principalKyat - sumMoneyKyat(loan.repayments.map((repayment) => repayment.amountKyat)))
}

export function formatKyat(amountKyat: number): string {
  return formatMoney(amountKyat)
}

export function formatLoanDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
