// Shared backup schedule/retention formatting for the site Backups tab.

import { cronToPicks } from './cron.ts'

export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export const formatTime = (hour, minute = 0) => {
  const suffix = hour < 12 ? 'AM' : 'PM'
  const display = hour % 12 === 0 ? 12 : hour % 12
  return `${display}:${String(minute).padStart(2, '0')} ${suffix}`
}

/** Label a UTC cron expression in the viewer's local time. */
export const cronToLabel = (cron) => {
  if (!cron) return ''
  const picks = cronToPicks(cron)
  const time = formatTime(picks.hour, picks.minute)
  if (picks.frequency === 'monthly') return `Monthly on day ${picks.monthDay}, ${time}`
  if (picks.frequency === 'weekly') return `Weekly on ${WEEKDAYS[picks.weekday]}, ${time}`
  return `Daily at ${time}`
}
