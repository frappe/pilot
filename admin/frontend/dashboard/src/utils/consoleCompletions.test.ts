import test from 'node:test'
import assert from 'node:assert/strict'

import { python } from '@codemirror/lang-python'
import { EditorState } from '@codemirror/state'

import { getConsoleCompletionContext, isInsideString } from './consoleCompletions.ts'

const stateOf = (doc: string) => EditorState.create({ doc, extensions: [python()] })

test('a cursor in a closed or open string is inside it', () => {
  assert.equal(isInsideString(stateOf('frappe.get_doc("ToDo", "")'), 24), true)
  assert.equal(isInsideString(stateOf('frappe.get_doc("ToDo", "'), 24), true)
})

test('a cursor in code or an f-string replacement is not inside a string', () => {
  assert.equal(isInsideString(stateOf('frappe.get_doc("ToDo", '), 23), false)
  assert.equal(isInsideString(stateOf('x = f"{name'), 11), false)
})

test('the first string argument of get_doc completes doctypes', () => {
  assert.deepEqual(getConsoleCompletionContext('frappe.get_doc("To', 18), { kind: 'doctype', from: 16 })
})
