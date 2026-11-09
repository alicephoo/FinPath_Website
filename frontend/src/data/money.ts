export type MoneyInputOptions = {
  allowNegative?: boolean
  allowZero?: boolean
  maxAmount?: number
}

/**
 * Maximum amount for a single financial entry: transaction income/expense,
 * loan principal or repayment, savings contribution/withdrawal, or trip
 * spending. Planned/settings amounts (starting balance, savings target,
 * daily-plan amount, trip budget) are not individual entries and use the
 * safe-integer bound instead.
 */
export const MAX_FINANCIAL_ENTRY_KYAT = 1_000_000_000

/** Largest exact whole-Kyat amount that can be represented safely in JSON. */
export const MAX_MONEY_KYAT = Number.MAX_SAFE_INTEGER

/**
 * Parse whole-Kyat input. Decimal values, cents, currency symbols, and
 * thousands separators are rejected rather than rounded or reinterpreted.
 */
export function parseMoneyToKyat(
  value: string,
  {
    allowNegative = false,
    allowZero = false,
    maxAmount = MAX_MONEY_KYAT,
  }: MoneyInputOptions = {},
): number | null {
  const match = /^(-?)(\d+)$/.exec(value.trim())
  if (!match) return null
  if (!Number.isSafeInteger(maxAmount) || maxAmount < 0) {
    throw new RangeError('The money limit must be a non-negative safe integer.')
  }

  const [, sign, wholeText] = match
  if (sign === '-' && !allowNegative) return null
  if (BigInt(wholeText) > BigInt(Number.MAX_SAFE_INTEGER)) return null

  const wholeKyat = BigInt(wholeText)
  const amountKyat = Number(sign === '-' ? -wholeKyat : wholeKyat)
  if (!Number.isSafeInteger(amountKyat)) return null
  if (Math.abs(amountKyat) > maxAmount) return null
  if (!allowZero && amountKyat === 0) return null
  return amountKyat
}

/** Format a whole-Kyat amount with grouping and an explicit MMK label. */
export function formatMoney(amountKyat: number): string {
  if (!Number.isSafeInteger(amountKyat)) {
    throw new RangeError('Money formatting requires a safe integer whole-Kyat amount.')
  }
  const formatted = new Intl.NumberFormat('en-US', {
    useGrouping: true,
    maximumFractionDigits: 0,
  }).format(Math.abs(amountKyat))
  return `${amountKyat < 0 ? '-' : ''}${formatted} MMK`
}

/** Render a whole-Kyat amount for an editable form field (no grouping). */
export function moneyToEntryString(amountKyat: number): string {
  if (!Number.isSafeInteger(amountKyat)) {
    throw new RangeError('Money form values require a safe integer whole-Kyat amount.')
  }
  return String(amountKyat)
}

/** Sum exact whole-Kyat amounts and reject unsafe inputs or totals. */
export function sumMoneyKyat(amounts: readonly number[]): number {
  let total = 0n
  for (const amount of amounts) {
    if (!Number.isSafeInteger(amount)) {
      throw new RangeError('Money sums require safe integer whole-Kyat amounts.')
    }
    total += BigInt(amount)
  }
  const result = Number(total)
  if (!Number.isSafeInteger(result)) {
    throw new RangeError('The money total exceeds the safe integer range.')
  }
  return result
}

/** Divide whole Kyat with half-up rounding using exact integer arithmetic. */
export function divideWholeKyatRounded(totalKyat: number, divisor: number): number {
  if (!Number.isSafeInteger(totalKyat)
    || !Number.isSafeInteger(divisor)
    || divisor <= 0) {
    throw new RangeError('Money division requires a safe integer amount and a positive integer divisor.')
  }
  const total = BigInt(totalKyat)
  const divisorBigInt = BigInt(divisor)
  const magnitude = total < 0n ? -total : total
  const quotient = magnitude / divisorBigInt
  const remainder = magnitude % divisorBigInt
  const roundedMagnitude = quotient + (remainder * 2n >= divisorBigInt ? 1n : 0n)
  const rounded = total < 0n ? -roundedMagnitude : roundedMagnitude
  const result = Number(rounded)
  if (!Number.isSafeInteger(result)) {
    throw new RangeError('The money result exceeds the safe integer range.')
  }
  return result
}

/**
 * Split a whole-Kyat total across a fixed number of parts without losing
 * Kyat. Earlier parts receive the remainder so the allocation always sums
 * back to the original total.
 */
export function allocateWholeKyat(totalKyat: number, parts: number): number[] {
  if (!Number.isSafeInteger(totalKyat) || totalKyat < 0) {
    throw new RangeError('Allocation requires a non-negative safe integer amount.')
  }
  if (!Number.isSafeInteger(parts) || parts <= 0) {
    throw new RangeError('Allocation requires a positive integer number of parts.')
  }

  const total = BigInt(totalKyat)
  const count = BigInt(parts)
  const base = total / count
  const remainder = total % count
  return Array.from({ length: parts }, (_, index) => {
    const amount = Number(base + (BigInt(index) < remainder ? 1n : 0n))
    if (!Number.isSafeInteger(amount)) {
      throw new RangeError('The allocated money amount exceeds the safe integer range.')
    }
    return amount
  })
}
