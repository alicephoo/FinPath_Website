import { describe, expect, it } from 'vitest'
import { addLocalDateDays, isValidDateOnly, localDateString } from './dateOnly'
import {
  allocateWholeKyat,
  divideWholeKyatRounded,
  formatMoney,
  MAX_FINANCIAL_ENTRY_KYAT,
  MAX_MONEY_KYAT,
  moneyToEntryString,
  parseMoneyToKyat,
  sumMoneyKyat,
} from './money'

describe('whole-Kyat money helpers', () => {
  it('parses whole-Kyat integers and ignores surrounding whitespace', () => {
    expect(parseMoneyToKyat(' 12 ')).toBe(12)
    expect(parseMoneyToKyat('000150000')).toBe(150_000)
    expect(MAX_FINANCIAL_ENTRY_KYAT).toBe(1_000_000_000)
    expect(MAX_MONEY_KYAT).toBe(Number.MAX_SAFE_INTEGER)
  })

  it.each([
    '',
    'abc',
    '12.34',
    '0.5',
    '.5',
    '1.',
    '1,000',
    '$1',
    '1e3',
    '1.2.3',
    'Infinity',
    'NaN',
    '+1',
    '-',
  ])('rejects non-whole or decorated monetary input %j', (value) => {
    expect(parseMoneyToKyat(value)).toBeNull()
  })

  it('rejects negative values and zero unless explicitly allowed', () => {
    expect(parseMoneyToKyat('-12')).toBeNull()
    expect(parseMoneyToKyat('0')).toBeNull()
    expect(parseMoneyToKyat('-12', { allowNegative: true })).toBe(-12)
    expect(parseMoneyToKyat('-0', { allowNegative: true, allowZero: true })).toBe(0)
    expect(parseMoneyToKyat('0', { allowZero: true })).toBe(0)
  })

  it('applies the individual-entry limit only when requested', () => {
    expect(parseMoneyToKyat('1000000001')).toBe(1_000_000_001)
    expect(parseMoneyToKyat('1000000000', { maxAmount: MAX_FINANCIAL_ENTRY_KYAT })).toBe(MAX_FINANCIAL_ENTRY_KYAT)
    expect(parseMoneyToKyat('1000000001', { maxAmount: MAX_FINANCIAL_ENTRY_KYAT })).toBeNull()
    expect(parseMoneyToKyat('-1000000001', { allowNegative: true, maxAmount: MAX_FINANCIAL_ENTRY_KYAT })).toBeNull()
  })

  it('accepts only safe whole-Kyat amounts at the JSON boundary', () => {
    expect(parseMoneyToKyat(String(Number.MAX_SAFE_INTEGER))).toBe(Number.MAX_SAFE_INTEGER)
    expect(parseMoneyToKyat('9007199254740992')).toBeNull()
    expect(parseMoneyToKyat('-9007199254740992', { allowNegative: true })).toBeNull()
    expect(parseMoneyToKyat('1', { maxAmount: 0 })).toBeNull()
    expect(() => parseMoneyToKyat('1', { maxAmount: 1.5 })).toThrow(RangeError)
  })

  it('formats whole Kyat with grouping and an explicit MMK label', () => {
    expect(formatMoney(0)).toBe('0 MMK')
    expect(formatMoney(1_234)).toBe('1,234 MMK')
    expect(formatMoney(-123_456)).toBe('-123,456 MMK')
    expect(formatMoney(Number.MAX_SAFE_INTEGER)).toBe('9,007,199,254,740,991 MMK')
    expect(() => formatMoney(1.5)).toThrow(RangeError)
    expect(() => formatMoney(Number.MAX_SAFE_INTEGER + 1)).toThrow(RangeError)
  })

  it('renders editable and CSV amounts as raw whole-Kyat integers', () => {
    expect(moneyToEntryString(1_234)).toBe('1234')
    expect(moneyToEntryString(-50)).toBe('-50')
    expect(() => moneyToEntryString(1.5)).toThrow(RangeError)
  })

  it('sums exact whole Kyat and rejects unsafe inputs or totals', () => {
    expect(sumMoneyKyat([])).toBe(0)
    expect(sumMoneyKyat([100, -25, 50])).toBe(125)
    expect(() => sumMoneyKyat([Number.MAX_SAFE_INTEGER, 1])).toThrow(RangeError)
    expect(() => sumMoneyKyat([Number.MAX_SAFE_INTEGER + 1])).toThrow(RangeError)
    expect(() => sumMoneyKyat([1.5])).toThrow(RangeError)
  })

  it('divides whole Kyat with deterministic half-up rounding', () => {
    expect(divideWholeKyatRounded(10, 4)).toBe(3)
    expect(divideWholeKyatRounded(9, 4)).toBe(2)
    expect(divideWholeKyatRounded(5, 2)).toBe(3)
    expect(divideWholeKyatRounded(-10, 4)).toBe(-3)
    expect(divideWholeKyatRounded(-9, 4)).toBe(-2)
    expect(() => divideWholeKyatRounded(100, 0)).toThrow(RangeError)
    expect(() => divideWholeKyatRounded(100, -2)).toThrow(RangeError)
    expect(() => divideWholeKyatRounded(100, 2.5)).toThrow(RangeError)
    expect(() => divideWholeKyatRounded(Number.MAX_SAFE_INTEGER + 1, 2)).toThrow(RangeError)
  })

  it('allocates a whole-Kyat total without losing or inventing Kyat', () => {
    expect(allocateWholeKyat(10, 4)).toEqual([3, 3, 2, 2])
    expect(allocateWholeKyat(1_000, 3)).toEqual([334, 333, 333])
    expect(allocateWholeKyat(1_000, 7).reduce((total, amount) => total + amount, 0)).toBe(1_000)
    expect(allocateWholeKyat(2, 5)).toEqual([1, 1, 0, 0, 0])
    expect(() => allocateWholeKyat(-1, 3)).toThrow(RangeError)
    expect(() => allocateWholeKyat(10, 0)).toThrow(RangeError)
    expect(() => allocateWholeKyat(10, 2.5)).toThrow(RangeError)
  })
})

describe('local date helpers', () => {
  it('formats a supplied date using its local calendar fields', () => {
    expect(localDateString(new Date(2026, 0, 1, 23, 59))).toBe('2026-01-01')
  })

  it.each([
    ['2024-02-29', true],
    ['2026-02-29', false],
    ['2026-04-31', false],
    ['2026-13-01', false],
    ['26-01-01', false],
    ['', false],
  ])('validates date-only input %s', (date, valid) => {
    expect(isValidDateOnly(date)).toBe(valid)
  })

  it.each([
    ['2026-01-31', 1, '2026-02-01'],
    ['2026-12-31', 1, '2027-01-01'],
    ['2024-02-28', 1, '2024-02-29'],
  ])('adds local days across calendar boundary %s', (date, days, expected) => {
    expect(addLocalDateDays(date, days)).toBe(expected)
  })
})
