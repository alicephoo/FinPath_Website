import { sumMoneyKyat } from './money'
import type { Transaction } from './transactionsMockData'

export type StartingBalance = {
  amountKyat: number
  effectiveDate: string
}

export function calculateAccountBalanceKyat(
  startingBalance: StartingBalance,
  transactions: readonly Transaction[],
): number {
  const eligibleTransactions = transactions
    .filter((transaction) => transaction.date > startingBalance.effectiveDate)
    .map((transaction) => transaction.type === 'income'
      ? transaction.amountKyat
      : -transaction.amountKyat)

  return sumMoneyKyat([startingBalance.amountKyat, ...eligibleTransactions])
}
