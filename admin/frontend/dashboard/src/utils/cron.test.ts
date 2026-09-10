import assert from 'node:assert/strict'
import test from 'node:test'

import { cronToPicks, picksToCron } from './cron.ts'

const picks = (over = {}) => ({
  frequency: 'daily',
  weekday: 0,
  monthDay: 1,
  hour: 2,
  minute: 0,
  ...over,
})

// A fixed reference date keeps the UTC offset lookup deterministic.
const REFERENCE = new Date('2026-08-26T00:00:00Z')

const inZone = (zone, run) => {
  const actual = process.env.TZ
  process.env.TZ = zone
  try {
    run()
  } finally {
    process.env.TZ = actual
  }
}

test('a half-hour-ahead zone converts the local hour into a UTC minute', () => {
  // 8:00 PM in Asia/Kolkata (UTC+5:30) is 14:30 UTC.
  inZone('Asia/Kolkata', () => {
    assert.equal(picksToCron(picks({ hour: 20 }), REFERENCE), '30 14 * * *')
    assert.equal(cronToPicks('30 14 * * *', REFERENCE).hour, 20)
    assert.equal(cronToPicks('30 14 * * *', REFERENCE).minute, 0)
  })
})

test('a UTC browser stores the local hour unchanged', () => {
  inZone('UTC', () => {
    assert.equal(picksToCron(picks({ hour: 20 }), REFERENCE), '0 20 * * *')
    assert.equal(cronToPicks('0 20 * * *', REFERENCE).hour, 20)
  })
})

test('a behind-UTC zone rolls the weekday forward when the UTC time crosses midnight', () => {
  // 8:00 PM Monday in America/New_York (UTC-4 in August) is 00:00 UTC on Tuesday.
  inZone('America/New_York', () => {
    assert.equal(picksToCron(picks({ frequency: 'weekly', weekday: 1, hour: 20 }), REFERENCE), '0 0 * * 2')
    assert.equal(cronToPicks('0 0 * * 2', REFERENCE).weekday, 1)
  })
})

test('an ahead-of-UTC zone rolls the weekday back when the UTC time crosses midnight', () => {
  // 1:00 AM Monday in Asia/Kolkata is 19:30 UTC on Sunday.
  inZone('Asia/Kolkata', () => {
    assert.equal(picksToCron(picks({ frequency: 'weekly', weekday: 1, hour: 1 }), REFERENCE), '30 19 * * 0')
    assert.equal(cronToPicks('30 19 * * 0', REFERENCE).weekday, 1)
  })
})

test('the day of month rolls with the same crossing', () => {
  inZone('America/New_York', () => {
    assert.equal(picksToCron(picks({ frequency: 'monthly', monthDay: 12, hour: 20 }), REFERENCE), '0 0 13 * *')
    assert.equal(cronToPicks('0 0 13 * *', REFERENCE).monthDay, 12)
  })
})

test('a day-one monthly schedule never rolls back to day 31', () => {
  // Rolling day 1 back would land on day 31 and skip every month without one.
  inZone('Asia/Kolkata', () => {
    const cron = picksToCron(picks({ frequency: 'monthly', monthDay: 1, hour: 1 }), REFERENCE)
    assert.equal(cron, '30 19 1 * *')
    // Held at day 1, the run lands a day later in local time, which is what the picker reads back.
    assert.equal(cronToPicks(cron, REFERENCE).monthDay, 2)
  })
})

test('a day-31 monthly schedule never rolls past the end of the month', () => {
  inZone('America/New_York', () => {
    const cron = picksToCron(picks({ frequency: 'monthly', monthDay: 31, hour: 20 }), REFERENCE)
    assert.equal(cron, '0 0 31 * *')
  })
})

test('a clamped monthly schedule stays put once it is read back', () => {
  inZone('Asia/Kolkata', () => {
    const first = picksToCron(picks({ frequency: 'monthly', monthDay: 1, hour: 1 }), REFERENCE)
    const reread = picksToCron(cronToPicks(first, REFERENCE), REFERENCE)
    assert.equal(reread, first)
  })
})

test('every frequency round-trips in a half-hour zone', () => {
  inZone('Asia/Kolkata', () => {
    for (const over of [
      { hour: 20 },
      { frequency: 'weekly', weekday: 3, hour: 6 },
      { frequency: 'monthly', monthDay: 12, hour: 23 },
    ]) {
      const wanted = picks(over)
      const back = cronToPicks(picksToCron(wanted, REFERENCE), REFERENCE)
      assert.equal(back.frequency, wanted.frequency)
      assert.equal(back.hour, wanted.hour)
      assert.equal(back.minute, wanted.minute)
      if (wanted.frequency === 'weekly') assert.equal(back.weekday, wanted.weekday)
      if (wanted.frequency === 'monthly') assert.equal(back.monthDay, wanted.monthDay)
    }
  })
})

test('an empty expression falls back to the defaults', () => {
  const back = cronToPicks('', REFERENCE)
  assert.equal(back.frequency, 'daily')
  assert.equal(back.hour, 2)
})

test('a malformed expression does not produce NaN', () => {
  const back = cronToPicks('x y * * *', REFERENCE)
  assert.equal(Number.isNaN(back.hour), false)
  assert.equal(Number.isNaN(back.minute), false)
})
