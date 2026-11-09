import { formatDateOnly } from './dateOnly'
import { formatMoney, sumMoneyKyat } from './money'

export type TripCategoryBudget = {
  id: string
  category: string
  budgetKyat: number
}

// Trip-owned spending is separate from transaction records; it has no transaction ID.
export type TripSpendingEntry = {
  id: string
  categoryBudgetId: string | null
  category: string
  description: string
  amountKyat: number
  date: string
}

export type Trip = {
  id: string
  name: string
  destination: string
  startDate: string
  endDate: string
  status: 'active' | 'completed' | 'cancelled'
  budgets: TripCategoryBudget[]
  spending: TripSpendingEntry[]
}

export function getTripBudgetKyat(trip: Trip): number {
  return sumMoneyKyat(trip.budgets.map((budget) => budget.budgetKyat))
}

export function getTripSpendingKyat(trip: Trip): number {
  return sumMoneyKyat(trip.spending.map((entry) => entry.amountKyat))
}

export function getCategorySpendingKyat(trip: Trip, categoryBudgetId: string): number {
  return sumMoneyKyat(trip.spending
    .filter((entry) => entry.categoryBudgetId === categoryBudgetId)
    .map((entry) => entry.amountKyat))
}

export function getBudgetProgressPercent(budgetKyat: number, spentKyat: number): number {
  if (budgetKyat <= 0) return spentKyat > 0 ? 100 : 0
  return Math.min(100, Math.round((spentKyat / budgetKyat) * 100))
}

export function formatTripKyat(amountKyat: number): string {
  return formatMoney(amountKyat)
}

export function formatTripDate(date: string): string {
  return formatDateOnly(date, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function formatTripDateParts(date: string): { day: string; month: string } {
  return {
    day: formatDateOnly(date, { day: '2-digit' }),
    month: formatDateOnly(date, { month: 'short' }).toUpperCase(),
  }
}
