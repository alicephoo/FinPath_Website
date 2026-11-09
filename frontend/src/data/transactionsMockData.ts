import { formatDateOnly } from './dateOnly'
import { formatMoney } from './money'

export type TransactionType = 'income' | 'expense'

export type Transaction = {
  id: string
  description: string
  type: TransactionType
  category: string
  amountKyat: number
  date: string
}

export const transactionCategories: Record<TransactionType, string[]> = {
  income: ['Salary', 'Freelance', 'Other income'],
  expense: ['Groceries', 'Transport', 'Housing', 'Utilities', 'Dining', 'Health', 'Shopping', 'Other'],
}

// Sample amounts are whole Myanmar Kyat (MMK); they are not USD-cent values.
export const sampleTransactions: Transaction[] = [
  { id: 'tx-01', description: 'Northstar Studio', category: 'Salary', type: 'income', amountKyat: 1_850_000, date: '2026-10-28' },
  { id: 'tx-02', description: 'Green Basket Market', category: 'Groceries', type: 'expense', amountKyat: 42_000, date: '2026-10-27' },
  { id: 'tx-03', description: 'Metro Transit', category: 'Transport', type: 'expense', amountKyat: 8_500, date: '2026-10-26' },
  { id: 'tx-04', description: 'Freelance project', category: 'Other income', type: 'income', amountKyat: 320_000, date: '2026-10-24' },
  { id: 'tx-05', description: 'Harborlight Utilities', category: 'Utilities', type: 'expense', amountKyat: 65_000, date: '2026-10-21' },
  { id: 'tx-06', description: 'Sunday Table', category: 'Dining', type: 'expense', amountKyat: 28_000, date: '2026-10-19' },
]

export function formatTransactionAmount(amountKyat: number): string {
  return formatMoney(amountKyat)
}

export function formatTransactionDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
  })
}