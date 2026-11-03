import assert from 'node:assert/strict'
import test from 'node:test'

import { castConfigValue, formatConfigValue, inferConfigType } from './siteConfig.ts'

test('castConfigValue casts each type', () => {
  assert.equal(castConfigValue('25', 'Number'), 25)
  assert.equal(castConfigValue('true', 'Boolean'), true)
  assert.equal(castConfigValue('false', 'Boolean'), false)
  assert.deepEqual(castConfigValue('{"a": [1]}', 'JSON'), { a: [1] })
  assert.equal(castConfigValue('587', 'String'), '587')
  assert.equal(castConfigValue('s3cret', 'Password'), 's3cret')
})

test('castConfigValue refuses a value that does not fit the type', () => {
  assert.throws(() => castConfigValue('ten', 'Number'), /must be a number/)
  assert.throws(() => castConfigValue(' ', 'Number'), /must be a number/)
  assert.throws(() => castConfigValue('yes', 'Boolean'), /true or false/)
  assert.throws(() => castConfigValue('{a: 1}', 'JSON'), /valid JSON/)
  assert.throws(() => castConfigValue('', 'Password'), /Enter a value/)
})

test('inferConfigType reads the type of a stored value', () => {
  assert.equal(inferConfigType(true), 'Boolean')
  assert.equal(inferConfigType(4), 'Number')
  assert.equal(inferConfigType({ a: 1 }), 'JSON')
  assert.equal(inferConfigType([1]), 'JSON')
  assert.equal(inferConfigType('x'), 'String')
  assert.equal(inferConfigType(null), 'String')
})

test('formatConfigValue shows a stored value in its input', () => {
  assert.equal(formatConfigValue(1, 'Boolean'), 'true')
  assert.equal(formatConfigValue(0, 'Boolean'), 'false')
  assert.equal(formatConfigValue({ a: 1 }, 'JSON'), '{\n  "a": 1\n}')
  assert.equal(formatConfigValue(587, 'String'), '587')
  assert.equal(formatConfigValue(undefined, 'String'), '')
})
