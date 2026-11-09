import { describe, expect, it } from 'vitest'
import { getOutstandingPrincipalKyat, sampleLoans } from './loansMockData'
import {
  MAX_FINANCIAL_ENTRY_KYAT,
  MAX_MONEY_KYAT,
  parseMoneyToKyat,
  sumMoneyKyat,
} from './money'

describe('loan principal calculations', () => {
  it('starts with the full whole-Kyat principal and subtracts partial repayments', () => {
    const loan = { ...sampleLoans[0], principalKyat: 10_000, repayments: [] }
    expect(getOutstandingPrincipalKyat(loan)).toBe(10_000)
    expect(getOutstandingPrincipalKyat({
      ...loan,
      repayments: [{ id: 'partial', amountKyat: 2_500, date: '2026-10-01', note: '' }],
    })).toBe(7_500)
  })

  it('reaches zero after exact payoff and never reports a negative balance', () => {
    const loan = {
      ...sampleLoans[0],
      principalKyat: 10_000,
      repayments: [{ id: 'paid', amountKyat: 10_000, date: '2026-10-01', note: '' }],
    }
    expect(getOutstandingPrincipalKyat(loan)).toBe(0)
    expect(getOutstandingPrincipalKyat({
      ...loan,
      repayments: [{ ...loan.repayments[0], amountKyat: 11_000 }],
    })).toBe(0)
  })

  it('accepts only whole-Kyat entries within the individual-entry limit', () => {
    expect(parseMoneyToKyat('1000000000', { maxAmount: MAX_FINANCIAL_ENTRY_KYAT })).toBe(1_000_000_000)
    expect(parseMoneyToKyat('1000000001', { maxAmount: MAX_FINANCIAL_ENTRY_KYAT })).toBeNull()
    expect(parseMoneyToKyat('125.50')).toBeNull()
    expect(parseMoneyToKyat('9007199254740992')).toBeNull()
    expect(parseMoneyToKyat('Infinity')).toBeNull()
    expect(MAX_FINANCIAL_ENTRY_KYAT).toBe(1_000_000_000)
    expect(MAX_MONEY_KYAT).toBe(Number.MAX_SAFE_INTEGER)
  })

  it('keeps sample principals and repayments as reasonable whole Kyat', () => {
    for (const loan of sampleLoans) {
      expect(Number.isSafeInteger(loan.principalKyat)).toBe(true)
      expect(loan.principalKyat).toBeGreaterThan(0)
      for (const repayment of loan.repayments) {
        expect(Number.isSafeInteger(repayment.amountKyat)).toBe(true)
        expect(repayment.amountKyat).toBeGreaterThan(0)
        expect(repayment.amountKyat).toBeLessThanOrEqual(MAX_FINANCIAL_ENTRY_KYAT)
      }
    }
    expect(sampleLoans[0].principalKyat).toBe(180_000_000)
  })

  it('rejects unsafe aggregate repayment totals', () => {
    expect(() => sumMoneyKyat([Number.MAX_SAFE_INTEGER, 1])).toThrow(RangeError)
    expect(() => getOutstandingPrincipalKyat({
      ...sampleLoans[0],
      principalKyat: 1,
      repayments: [
        { id: 'one', amountKyat: Number.MAX_SAFE_INTEGER, date: '2026-10-01', note: '' },
        { id: 'two', amountKyat: 1, date: '2026-10-02', note: '' },
      ],
    })).toThrow(RangeError)
  })
})
