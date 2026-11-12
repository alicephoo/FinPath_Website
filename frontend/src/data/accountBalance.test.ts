import { describe, expect, it } from 'vitest'
import { calculateAccountBalanceKyat } from './accountBalance'
import type { Transaction } from './transactionsMockData'

const startingBalance = { amountKyat: 10_000, effectiveDate: '2026-10-10' }
const transaction = (
  id: string,
  type: Transaction['type'],
  amountKyat: number,
  date: string,
): Transaction => ({ id, type, amountKyat, date, category: 'Other', description: id })

describe('calculateAccountBalanceKyat', () => {
  it('returns the whole-Kyat starting balance when there are no transactions', () => {
    expect(calculateAccountBalanceKyat(startingBalance, [])).toBe(10_000)
  })

  it('accepts zero and negative starting balances', () => {
    expect(calculateAccountBalanceKyat({ ...startingBalance, amountKyat: 0 }, [])).toBe(0)
    expect(calculateAccountBalanceKyat({ ...startingBalance, amountKyat: -25_000 }, [])).toBe(-25_000)
    expect(calculateAccountBalanceKyat(
      { ...startingBalance, amountKyat: -25_000 },
      [transaction('income', 'income', 40_000, '2026-10-11')],
    )).toBe(15_000)
  })

  it('adds income and subtracts expenses on or after the effective date', () => {
    const transactions = [
      transaction('before', 'income', 5000, '2026-10-09'),
      transaction('same-day', 'expense', 2000, '2026-10-10'),
      transaction('income', 'income', 4000, '2026-10-11'),
      transaction('expense', 'expense', 1250, '2026-10-12'),
    ]
    expect(calculateAccountBalanceKyat(startingBalance, transactions)).toBe(10_750)
  })

  it('reflects edits and removals to eligible transactions', () => {
    const original = transaction('later', 'income', 3000, '2026-10-11')
    expect(calculateAccountBalanceKyat(startingBalance, [original])).toBe(13_000)
    expect(calculateAccountBalanceKyat(startingBalance, [{ ...original, amountKyat: 500 }])).toBe(10_500)
    expect(calculateAccountBalanceKyat(startingBalance, [])).toBe(10_000)
  })

  it('throws rather than returning an unsafe aggregate balance', () => {
    expect(() => calculateAccountBalanceKyat(
      { amountKyat: Number.MAX_SAFE_INTEGER, effectiveDate: '2026-10-10' },
      [transaction('later', 'income', 1, '2026-10-11')],
    )).toThrow(RangeError)
  })
})
