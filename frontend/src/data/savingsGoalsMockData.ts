import { addLocalDateDays, isValidDateOnly } from './dateOnly'
import {
  allocateWholeKyat,
  divideWholeKyatRounded,
  formatMoney,
  MAX_MONEY_KYAT,
  sumMoneyKyat,
} from './money'

export { localDateString } from './dateOnly'

export type SavingsEntryType = 'contribution' | 'withdrawal'

export type SavingsEntry = {
  id: string
  type: SavingsEntryType
  amountKyat: number
  date: string
  note: string
}

export type SavingsPlanDayStatus = 'pending' | 'completed' | 'missed'

export type SavingsPlanDay = {
  id: string
  dayNumber: number
  plannedDate: string
  baseAmountKyat: number
  carriedForwardKyat: number
  requiredAmountKyat: number
  status: SavingsPlanDayStatus
  completionDate?: string
  actualAmountKyat?: number
}

export type SavingsDailyPlan = {
  startDate: string
  durationDays: number
  dailyAmountKyat: number
  /**
   * How the schedule was built. `target` means the goal target was allocated
   * across the days exactly; `fixed` means each day uses the same daily amount.
   */
  allocationMode?: 'target' | 'fixed'
  days: SavingsPlanDay[]
}

export type SavingsGoal = {
  id: string
  name: string
  targetKyat: number
  tone: 'mint' | 'gold' | 'blue'
  entries: SavingsEntry[]
  dailyPlan?: SavingsDailyPlan
}

export const MAX_SAVINGS_PLAN_DURATION_DAYS = 365

// Sample amounts are whole Myanmar Kyat (MMK); they are not USD-cent values.
export const sampleSavingsGoals: SavingsGoal[] = [
  {
    id: 'goal-rainy-day',
    name: 'Rainy day fund',
    targetKyat: 12_000_000,
    tone: 'mint',
    entries: [
      { id: 'entry-rainy-day-opening', type: 'contribution', amountKyat: 8_400_000, date: '2026-10-01', note: 'Sample opening balance' },
    ],
  },
  {
    id: 'goal-new-home',
    name: 'New home',
    targetKyat: 30_000_000,
    tone: 'gold',
    entries: [
      { id: 'entry-new-home-opening', type: 'contribution', amountKyat: 16_250_000, date: '2026-10-01', note: 'Sample opening balance' },
    ],
  },
  {
    id: 'goal-camera-kit',
    name: 'Camera kit',
    targetKyat: 2_000_000,
    tone: 'blue',
    entries: [
      { id: 'entry-camera-kit-opening', type: 'contribution', amountKyat: 1_180_000, date: '2026-10-01', note: 'Sample opening balance' },
    ],
  },
]

export function getSavingsBalanceKyat(goal: SavingsGoal): number {
  return sumMoneyKyat(goal.entries.map((entry) => (
    entry.type === 'contribution' ? entry.amountKyat : -entry.amountKyat
  )))
}

export function getSavingsProgress(goal: SavingsGoal): number {
  if (goal.targetKyat <= 0) return 0
  return Math.min(100, Math.round((getSavingsBalanceKyat(goal) / goal.targetKyat) * 100))
}

export function isValidLocalDate(value: string): boolean {
  return isValidDateOnly(value)
}

export function addLocalDays(dateString: string, days: number): string {
  return addLocalDateDays(dateString, days)
}

/**
 * Create a daily savings schedule. When a target is supplied, the target is
 * allocated exactly across the days (earlier days receive the remainder) so
 * the planned total never drifts because of rounding. Otherwise the same
 * whole-Kyat daily amount is repeated.
 */
export function createSavingsDailyPlan(
  goalId: string,
  startDate: string,
  durationDays: number,
  dailyAmountKyat: number,
  targetKyat?: number,
): SavingsDailyPlan {
  if (!Number.isSafeInteger(durationDays)
    || durationDays < 1
    || durationDays > MAX_SAVINGS_PLAN_DURATION_DAYS) {
    throw new RangeError(`Savings plans must last between 1 and ${MAX_SAVINGS_PLAN_DURATION_DAYS} days.`)
  }

  let baseAmounts: number[]
  if (targetKyat !== undefined) {
    if (!Number.isSafeInteger(targetKyat)
      || targetKyat <= 0
      || targetKyat > MAX_MONEY_KYAT) {
      throw new RangeError('The savings target must be a positive safe integer whole-Kyat amount.')
    }
    baseAmounts = allocateWholeKyat(targetKyat, durationDays)
  } else {
    if (!Number.isSafeInteger(dailyAmountKyat) || dailyAmountKyat <= 0) {
      throw new RangeError('The daily savings amount must be a positive safe integer whole-Kyat amount.')
    }
    baseAmounts = Array.from({ length: durationDays }, () => dailyAmountKyat)
  }

  const nominalDailyAmount = targetKyat !== undefined
    ? divideWholeKyatRounded(targetKyat, durationDays)
    : dailyAmountKyat
  const days = baseAmounts.map((baseAmountKyat, index) => {
    const plannedDate = addLocalDays(startDate, index)
    return {
      id: `${goalId}:${plannedDate}`,
      dayNumber: index + 1,
      plannedDate,
      baseAmountKyat,
      carriedForwardKyat: 0,
      requiredAmountKyat: baseAmountKyat,
      status: 'pending' as const,
    }
  })
  return {
    startDate,
    durationDays,
    dailyAmountKyat: nominalDailyAmount,
    allocationMode: targetKyat !== undefined ? 'target' : 'fixed',
    days,
  }
}

export function recalculateSavingsDailyPlan(plan: SavingsDailyPlan): SavingsDailyPlan {
  let outstandingKyat = 0
  const days = plan.days.map((day) => {
    const carriedForwardKyat = outstandingKyat
    const requiredAmountKyat = sumMoneyKyat([day.baseAmountKyat, carriedForwardKyat])

    if (day.status === 'missed') {
      outstandingKyat = Math.max(0, sumMoneyKyat([requiredAmountKyat, -(day.actualAmountKyat ?? 0)]))
    } else if (day.status === 'completed') {
      outstandingKyat = Math.max(0, sumMoneyKyat([outstandingKyat, -(day.actualAmountKyat ?? 0)]))
    }

    return { ...day, carriedForwardKyat, requiredAmountKyat }
  })
  return { ...plan, days }
}

export function getOutstandingSavingsKyat(plan: SavingsDailyPlan): number {
  let outstandingKyat = 0
  for (const day of plan.days) {
    const requiredKyat = sumMoneyKyat([day.baseAmountKyat, outstandingKyat])
    if (day.status === 'missed') {
      outstandingKyat = Math.max(0, sumMoneyKyat([requiredKyat, -(day.actualAmountKyat ?? 0)]))
    } else if (day.status === 'completed') {
      outstandingKyat = Math.max(0, sumMoneyKyat([outstandingKyat, -(day.actualAmountKyat ?? 0)]))
    }
  }
  return outstandingKyat
}

export function formatSavingsKyat(amountKyat: number): string {
  return formatMoney(amountKyat)
}
