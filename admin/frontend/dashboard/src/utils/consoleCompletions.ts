import { syntaxTree } from '@codemirror/language'
import type { EditorState } from '@codemirror/state'

/** Frappe-aware suggestions at the cursor, and where the typed word starts. Anything else is left to Jedi. */
export type ConsoleCompletionContext =
  | { kind: 'doctype'; from: number }
  | { kind: 'field'; doctype: string; from: number }
  | { kind: 'member'; expression: string; from: number }

// An open string as the first argument of a call that takes a doctype.
const DOCTYPE_ARGUMENT =
  /\b(?:get_doc|new_doc|get_cached_doc|get_last_doc|get_meta|delete_doc|get_all|get_list|get_value|set_value|get_values|get_cached_value|get_single_value|exists|count|DocType)\(\s*["']([^"'\n]*)$/

// An open string later in a call whose first argument named a doctype.
const FIELD_ARGUMENT =
  /\b(?:get_all|get_list|get_value|set_value|get_values|get_cached_value|get_single_value|count)\(\s*["']([^"'\n]+)["'][^()]*["'](\w*)$/

// `name.`, `name[0].`, or `name.child_table[0].`, with the attribute typed so far.
const MEMBER_ACCESS = /([A-Za-z_]\w*(?:\[[^[\]\n]*\]|\.[A-Za-z_]\w*)*)\.(\w*)$/

export const getConsoleCompletionContext = (
  code: string,
  cursor: number,
): ConsoleCompletionContext | null => {
  const before = code.slice(0, cursor)
  const doctype = before.match(DOCTYPE_ARGUMENT)
  if (doctype) return { kind: 'doctype', from: cursor - doctype[1].length }
  const field = before.match(FIELD_ARGUMENT)
  if (field) return { kind: 'field', doctype: field[1], from: cursor - field[2].length }

  const member = before.match(MEMBER_ACCESS)
  if (!member || /^frappe\b/.test(member[1])) return null
  return { kind: 'member', expression: member[1], from: cursor - member[2].length }
}

/** Whether the cursor is inside a string literal, where Jedi only offers file paths. */
export const isInsideString = (state: EditorState, pos: number): boolean =>
  syntaxTree(state).resolveInner(pos, -1).name === 'String'
