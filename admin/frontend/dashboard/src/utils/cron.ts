export type Frequency = 'daily' | 'weekly' | 'monthly'

export type SchedulePicks = {
  frequency: Frequency
  weekday: number
  monthDay: number
  hour: number
  minute: number
}

const DAYS_IN_WEEK = 7
const MAX_MONTH_DAY = 31

export const DEFAULT_PICKS: SchedulePicks = {
  frequency: 'daily',
  weekday: 0,
  monthDay: 1,
  hour: 2,
  minute: 0,
}

const toInt = (value: string | undefined, fallback = 0) => {
  const parsed = parseInt(value ?? '', 10)
  return isNaN(parsed) ? fallback : parsed
}

const wrapWeekday = (day: number) => ((day % DAYS_IN_WEEK) + DAYS_IN_WEEK) % DAYS_IN_WEEK

// Clamped, never wrapped: rolling day 1 back to 31 would skip every month without a 31st, so a
// schedule that crosses a month boundary keeps its day and fires a day either side instead.
const clampMonthDay = (day: number) => Math.min(MAX_MONTH_DAY, Math.max(1, day))

// Whether the UTC calendar day of `moment` is behind, level with, or ahead of its local day.
const dayOffset = (moment: Date) => {
  const difference = moment.getUTCDay() - moment.getDay()
  if (difference === DAYS_IN_WEEK - 1) return -1
  if (difference === 1 - DAYS_IN_WEEK) return 1
  return difference
}

/** The UTC cron expression that fires at the local time these picks describe.
 *
 * Cron entries run in the server's timezone, which Pilot keeps as UTC, while the pickers work in
 * the browser's local time. The offset is the one in effect on `reference`, so a fixed expression
 * cannot follow a later DST transition; that needs a timezone stored alongside the schedule.
 */
export const picksToCron = (picks: SchedulePicks, reference = new Date()): string => {
  const moment = new Date(reference.getTime())
  moment.setHours(picks.hour, picks.minute, 0, 0)

  const time = `${moment.getUTCMinutes()} ${moment.getUTCHours()}`
  const offset = dayOffset(moment)
  if (picks.frequency === 'weekly') return `${time} * * ${wrapWeekday(picks.weekday + offset)}`
  if (picks.frequency === 'monthly') return `${time} ${clampMonthDay(picks.monthDay + offset)} * *`
  return `${time} * * *`
}

/** The local picks a UTC cron expression fires at. */
export const cronToPicks = (cron: string, reference = new Date()): SchedulePicks => {
  if (!cron) return { ...DEFAULT_PICKS }
  const [minuteField, hourField, monthDayField, , weekdayField] = cron.trim().split(/\s+/)

  const moment = new Date(reference.getTime())
  moment.setUTCHours(toInt(hourField), toInt(minuteField), 0, 0)
  const offset = -dayOffset(moment)

  const picks: SchedulePicks = {
    ...DEFAULT_PICKS,
    hour: moment.getHours(),
    minute: moment.getMinutes(),
  }
  if (monthDayField !== '*') {
    picks.frequency = 'monthly'
    picks.monthDay = clampMonthDay(toInt(monthDayField, 1) + offset)
  } else if (weekdayField !== '*') {
    picks.frequency = 'weekly'
    picks.weekday = wrapWeekday(toInt(weekdayField) + offset)
  }
  return picks
}
