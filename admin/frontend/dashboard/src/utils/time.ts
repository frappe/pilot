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

export const relativeTime = (value) => {
  const seconds = (new Date(value).getTime() - Date.now()) / 1000
  const [unit, size] = UNITS.find(([, size]) => Math.abs(seconds) >= size) ?? UNITS.at(-1)

  return formatter.format(Math.round(seconds / size), unit)
}
