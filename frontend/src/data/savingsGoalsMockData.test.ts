import { describe, expect, it } from 'vitest'
import {
  addLocalDays,
  createSavingsDailyPlan,
  getOutstandingSavingsKyat,
  getSavingsBalanceKyat,
  getSavingsProgress,
  MAX_SAVINGS_PLAN_DURATION_DAYS,
  recalculateSavingsDailyPlan,
  type SavingsDailyPlan,
} from './savingsGoalsMockData'
import { sumMoneyKyat } from './money'

describe('daily savings plans', () => {
  it('creates one-day and 365-day fixed schedules with local dates and stable identifiers', () => {
    const oneDay = createSavingsDailyPlan('goal', '2026-12-31', 1, 500)
    expect(oneDay.days).toHaveLength(1)
    expect(oneDay.days[0]).toMatchObject({
      id: 'goal:2026-12-31',
      dayNumber: 1,
      plannedDate: '2026-12-31',
      requiredAmountKyat: 500,
      status: 'pending',
    })
    expect(oneDay.allocationMode).toBe('fixed')

    const year = createSavingsDailyPlan('goal', '2026-01-01', MAX_SAVINGS_PLAN_DURATION_DAYS, 100)
    expect(year.days).toHaveLength(365)
    expect(year.days[year.days.length - 1].plannedDate).toBe('2026-12-31')
  })

  it('allocates a target exactly across the plan without cent rounding drift', () => {
    const plan = createSavingsDailyPlan('goal', '2026-01-01', 3, 1, 1_000)
    expect(plan.allocationMode).toBe('target')
    expect(plan.days.map((day) => day.baseAmountKyat)).toEqual([334, 333, 333])
    expect(sumMoneyKyat(plan.days.map((day) => day.baseAmountKyat))).toBe(1_000)
    expect(plan.dailyAmountKyat).toBe(333)

    const week = createSavingsDailyPlan('goal', '2026-01-01', 7, 1, 1_000)
    expect(week.days.map((day) => day.baseAmountKyat)).toEqual([143, 143, 143, 143, 143, 143, 142])
    expect(sumMoneyKyat(week.days.map((day) => day.baseAmountKyat))).toBe(1_000)
  })

  it.each([0, -1, 1.5, 366, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid plan duration %s before schedule allocation',
    (duration) => expect(() => createSavingsDailyPlan('goal', '2026-01-01', duration, 100)).toThrow(RangeError),
  )

  it.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid fixed daily amount %s',
    (dailyAmount) => expect(() => createSavingsDailyPlan('goal', '2026-01-01', 2, dailyAmount)).toThrow(RangeError),
  )

  it.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid target amount %s for target allocation',
    (target) => expect(() => createSavingsDailyPlan('goal', '2026-01-01', 2, 1, target)).toThrow(RangeError),
  )

  it('uses local calendar arithmetic for month and year boundaries', () => {
    expect(addLocalDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(createSavingsDailyPlan('goal', '2026-02-28', 2, 100).days[1].plannedDate).toBe('2026-03-01')
  })

  it('carries missed-day amounts forward and clears the commitment after completion', () => {
    const plan = createSavingsDailyPlan('goal', '2026-10-01', 3, 1_000)
    const missed: SavingsDailyPlan = {
      ...plan,
      days: plan.days.map((day, index) => index === 0
        ? { ...day, status: 'missed', actualAmountKyat: 0 }
        : day),
    }
    const recalculatedMiss = recalculateSavingsDailyPlan(missed)
    expect(recalculatedMiss.days[1].requiredAmountKyat).toBe(2_000)
    expect(getOutstandingSavingsKyat(recalculatedMiss)).toBe(1_000)

    const completed: SavingsDailyPlan = {
      ...recalculatedMiss,
      days: recalculatedMiss.days.map((day, index) => index === 1
        ? { ...day, status: 'completed', actualAmountKyat: 2_000 }
        : day),
    }
    const recalculatedCompletion = recalculateSavingsDailyPlan(completed)
    expect(recalculatedCompletion.days[2].requiredAmountKyat).toBe(1_000)
    expect(getOutstandingSavingsKyat(recalculatedCompletion)).toBe(0)
  })

  it('preserves same-day contributions when recalculating a missed day', () => {
    const plan = createSavingsDailyPlan('goal', '2026-10-01', 2, 1_000)
    const withPartialSaving: SavingsDailyPlan = {
      ...plan,
      days: plan.days.map((day, index) => index === 0
        ? { ...day, status: 'missed', actualAmountKyat: 400 }
        : day),
    }
    const recalculated = recalculateSavingsDailyPlan(withPartialSaving)
    expect(recalculated.days[1].carriedForwardKyat).toBe(600)
    expect(recalculated.days[1].requiredAmountKyat).toBe(1_600)
  })
})

describe('savings balance calculations', () => {
  it('sums whole-Kyat contributions less withdrawals and computes progress', () => {
    const goal = {
      id: 'goal',
      name: 'Goal',
      targetKyat: 10_000,
      tone: 'mint' as const,
      entries: [
        { id: 'a', type: 'contribution' as const, amountKyat: 8_000, date: '2026-10-01', note: '' },
        { id: 'b', type: 'withdrawal' as const, amountKyat: 1_500, date: '2026-10-02', note: '' },
      ],
    }
    expect(getSavingsBalanceKyat(goal)).toBe(6_500)
    expect(getSavingsProgress(goal)).toBe(65)
  })

  it('throws rather than returning an unsafe net balance', () => {
    expect(() => getSavingsBalanceKyat({
      id: 'goal',
      name: 'Overflow',
      targetKyat: 1,
      tone: 'mint',
      entries: [
        { id: 'a', type: 'contribution', amountKyat: Number.MAX_SAFE_INTEGER, date: '2026-10-01', note: '' },
        { id: 'b', type: 'contribution', amountKyat: 1, date: '2026-10-02', note: '' },
      ],
    })).toThrow(RangeError)
  })
})
