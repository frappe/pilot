import type { DocTypeField } from '@/types/siteConsole'

/** What a console expression holds, as far as the code before the cursor shows. */
export type ConsoleValue =
  | { kind: 'document'; doctype: string }
  | { kind: 'row'; doctype: string; fields: string[] | null }
  | { kind: 'list'; item: ConsoleValue | null }

export type FieldLookup = (doctype: string) => Promise<DocTypeField[]>

const DOCUMENT_CALL = /^frappe\.(?:get_doc|new_doc|get_cached_doc|get_last_doc)\(\s*["']([^"'\n]+)["']/
const ROWS_CALL = /^frappe\.(?:db\.)?(?:get_all|get_list)\(\s*["']([^"'\n]+)["']/
const EXPRESSION = /^([A-Za-z_]\w*)((?:\[[^[\]\n]*\]|\.[A-Za-z_]\w*)*)$/
const STEP = /\[[^[\]\n]*\]|\.\w+/g
const TABLE_FIELD_TYPES = new Set(['Table', 'Table MultiSelect'])
const MAX_DEPTH = 8

/** Follow `name`, `name[0]`, and `name.child_table` back to a Frappe call or loop. */
export const resolveConsoleValue = async (
  code: string,
  expression: string,
  getFields: FieldLookup,
  depth = 0,
): Promise<ConsoleValue | null> => {
  const match = expression.match(EXPRESSION)
  if (!match || depth > MAX_DEPTH) return null
  let value = await resolveVariable(code, match[1], getFields, depth)
  for (const step of match[2].match(STEP) ?? []) {
    if (!value) return null
    if (step.startsWith('[')) value = value.kind === 'list' ? value.item : null
    else value = await resolveChildTable(value, step.slice(1), getFields)
  }
  return value
}

const resolveVariable = async (
  code: string,
  name: string,
  getFields: FieldLookup,
  depth: number,
): Promise<ConsoleValue | null> => {
  const assignment = lastMatch(code, new RegExp(`^[ \\t]*${name}[ \\t]*=(?!=)[ \\t]*`, 'gm'))
  const loop = lastMatch(code, new RegExp(`^[ \\t]*for[ \\t]+${name}[ \\t]+in[ \\t]+(.+?)[ \\t]*:`, 'gm'))
  if (loop && (!assignment || loop.index > assignment.index)) {
    const iterable = await resolveConsoleValue(code.slice(0, loop.index), loop[1], getFields, depth + 1)
    return iterable?.kind === 'list' ? iterable.item : null
  }
  if (!assignment) return null
  const source = code.slice(assignment.index + assignment[0].length)
  const document = source.match(DOCUMENT_CALL)
  if (document) return { kind: 'document', doctype: document[1] }
  const rows = source.match(ROWS_CALL)
  if (rows) return { kind: 'list', item: getRowValue(rows[1], getCallText(source)) }
  // Earlier code only, so `doc = doc.items[0]` reads the previous `doc`.
  const earlier = code.slice(0, assignment.index)
  return resolveConsoleValue(earlier, source.split('\n')[0].trim(), getFields, depth + 1)
}

const resolveChildTable = async (
  value: ConsoleValue,
  fieldname: string,
  getFields: FieldLookup,
): Promise<ConsoleValue | null> => {
  if (value.kind !== 'document') return null
  const field = (await getFields(value.doctype)).find((f) => f.fieldname === fieldname)
  if (!field || !TABLE_FIELD_TYPES.has(field.fieldtype) || !field.options) return null
  return { kind: 'list', item: { kind: 'document', doctype: field.options } }
}

/** `get_all` rows hold only the requested fields: `name` by default, every field for `"*"`. */
export const getRowValue = (doctype: string, call: string): ConsoleValue | null => {
  if (/\bpluck\s*=/.test(call)) return null
  const requested = call.match(/\bfields\s*=\s*\[([^\]]*)\]/)
  if (!requested) return { kind: 'row', doctype, fields: ['name'] }
  const names = [...requested[1].matchAll(/["']([^"']+)["']/g)].map((m) => m[1].trim())
  if (names.includes('*')) return { kind: 'row', doctype, fields: null }
  const fields = names
    .map((name) => name.match(/\bas\s+(\w+)$/i)?.[1] ?? name)
    .filter((name) => /^\w+$/.test(name))
  return { kind: 'row', doctype, fields }
}

/** The call at the start of `source`, up to its closing parenthesis. */
const getCallText = (source: string): string => {
  let depth = 0
  for (let index = 0; index < source.length; index++) {
    if (source[index] === '(') depth++
    else if (source[index] === ')' && --depth === 0) return source.slice(0, index + 1)
  }
  return source
}

const lastMatch = (code: string, pattern: RegExp): RegExpExecArray | null => {
  let last: RegExpExecArray | null = null
  for (const match of code.matchAll(pattern)) last = match as RegExpExecArray
  return last
}
