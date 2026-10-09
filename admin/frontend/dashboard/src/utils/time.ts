const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31536000],
  ['month', 2592000],
  ['week', 604800],
  ['day', 86400],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

export const relativeTime = (value: string | number | Date) => {
  const seconds = (new Date(value).getTime() - Date.now()) / 1000
  const [unit, size] =
    UNITS.find(([, size]) => Math.abs(seconds) >= size) ?? UNITS[UNITS.length - 1]

  return formatter.format(Math.round(seconds / size), unit)
}

/** frappe-ui time axes read a Date or an ISO string, so epoch milliseconds are dropped as rows with no date. */
export const withDateTime = <T extends { time?: number | null }>(rows: T[]) =>
  rows.map((row) => ({ ...row, time: row.time == null ? row.time : new Date(row.time) }))
