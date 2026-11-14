import type { Completion, CompletionContext, CompletionSource } from '@codemirror/autocomplete'
import { type Ref, watch } from 'vue'
import { consoleApi } from '@/api/console'
import { sitesApi } from '@/api/sites'
import { completionDetail, docstringNode } from '@/components/common/completionKinds'
import type { ConsoleCompletion } from '@/types/console'
import type { ConsoleDocType, DocTypeField } from '@/types/siteConsole'
import {
  type ConsoleCompletionContext,
  getConsoleCompletionContext,
  isInsideString,
} from '@/utils/consoleCompletions'
import { type ConsoleValue, resolveConsoleValue } from '@/utils/consoleValues'

const methods = (names: string[]): Completion[] => names.map((label) => ({ label, type: 'method' }))
const LIST_METHODS = methods([
  'append',
  'clear',
  'copy',
  'count',
  'extend',
  'index',
  'insert',
  'pop',
  'remove',
  'reverse',
  'sort',
])
// get_all rows are frappe._dict, so attribute access and dict methods both work.
const ROW_METHODS = methods([
  'copy',
  'get',
  'items',
  'keys',
  'pop',
  'setdefault',
  'update',
  'values',
])
const NO_DOCTYPE: ConsoleDocType = { fields: [], controller: '' }
const DOCUMENT_ASSIGNMENT =
  /^[ \t]*\w+[ \t]*=[ \t]*frappe\.(?:get_doc|new_doc|get_cached_doc|get_last_doc)\(\s*["']([^"'\n]+)["']/gm
// Ask Jedi before a word is typed only after a dot or in an import list.
const JEDI_TRIGGER = /(\.|\bimport\s+|\bimport\s.*,\s*)$/

const fieldOption = (field: DocTypeField): Completion => ({
  label: field.fieldname,
  type: 'property',
  detail:
    field.options && field.fieldtype.startsWith('Table')
      ? `${field.fieldtype} of ${field.options}`
      : field.fieldtype,
  info: field.label || undefined,
})

/** Console editor suggestions: Frappe-aware ones from the selected site, Jedi for Python itself. */
export const useConsoleCompletions = (site: Ref<string>) => {
  let cache = new Map<string, Promise<unknown>>()
  watch(site, () => (cache = new Map()))

  // A failed load is not kept, so the next suggestion request tries again.
  const load = <T>(key: string, fetch: () => Promise<T>, empty: T): Promise<T> => {
    let loaded = cache.get(key) as Promise<T> | undefined
    if (!loaded) {
      loaded = fetch().catch((error) => {
        console.warn(`Console suggestions for ${key} are unavailable:`, error)
        cache.delete(key)
        return empty
      })
      cache.set(key, loaded)
    }
    return loaded
  }

  const getDoctypes = () =>
    load('doctypes', () => sitesApi.consoleDoctypes(site.value), [] as string[])
  const getDoctype = (doctype: string) =>
    load(`doctype:${doctype}`, () => sitesApi.consoleDoctype(site.value, doctype), NO_DOCTYPE)
  const getFields = async (doctype: string) => (await getDoctype(doctype)).fields

  const getValueOptions = async (value: ConsoleValue): Promise<Completion[]> => {
    if (value.kind === 'list') return LIST_METHODS
    const { fields } = await getDoctype(value.doctype)
    if (value.kind === 'document') return fields.map(fieldOption)
    const rowFields = value.fields
      ? value.fields.map(
          (name) =>
            fields.find((f) => f.fieldname === name) ?? {
              fieldname: name,
              fieldtype: '',
              label: '',
              options: '',
            },
        )
      : fields
    return [...rowFields.map(fieldOption), ...ROW_METHODS]
  }

  const getFrappeOptions = async (
    found: ConsoleCompletionContext,
    code: string,
  ): Promise<Completion[]> => {
    if (found.kind === 'doctype')
      return (await getDoctypes()).map((label) => ({ label, type: 'class' }))
    if (found.kind === 'field') return (await getFields(found.doctype)).map(fieldOption)
    const value = await resolveConsoleValue(code, found.expression, getFields)
    return value ? getValueOptions(value) : []
  }

  // Controller classes of the doctypes the code loads, so Jedi can type those documents.
  const getControllers = async (code: string): Promise<Record<string, string>> => {
    const doctypes = new Set([...code.matchAll(DOCUMENT_ASSIGNMENT)].map((match) => match[1]))
    const loaded = await Promise.all(
      [...doctypes].map(async (doctype) => [doctype, (await getDoctype(doctype)).controller]),
    )
    return Object.fromEntries(loaded.filter(([, controller]) => controller))
  }

  const getJediOptions = async (context: CompletionContext): Promise<Completion[]> => {
    const code = context.state.doc.toString()
    const line = context.state.doc.lineAt(context.pos)
    const position = [
      code,
      line.number,
      context.pos - line.from,
      await getControllers(code),
    ] as const
    const found = await consoleApi.completions(...position).catch(() => [] as ConsoleCompletion[])
    return found.map((item) => ({
      ...item,
      type: item.type === 'function' ? 'method' : item.type,
      detail: completionDetail(item.detail),
      // Loaded only when the item is highlighted.
      info: async () => {
        const docstring = (await consoleApi.docstring(...position, item.label).catch(() => null))
          ?.docstring
        return docstring ? docstringNode(docstring) : null
      },
    }))
  }

  const source: CompletionSource = async (context) => {
    if (!site.value) return null
    const code = context.state.sliceDoc(0, context.pos)
    const found = getConsoleCompletionContext(code, context.pos)
    const frappeOptions = found ? await getFrappeOptions(found, code) : []
    // Doctype names and fields live in strings, where Jedi has nothing to add.
    if (found && found.kind !== 'member' && frappeOptions.length) {
      return {
        from: found.from,
        options: frappeOptions,
        validFor: found.kind === 'doctype' ? /^[\w ]*$/ : /^\w*$/,
      }
    }
    if (isInsideString(context.state, context.pos)) return null

    const word = context.matchBefore(/\w*/) ?? { from: context.pos, to: context.pos }
    const lineStart = context.state.doc.lineAt(context.pos).from
    const isTriggered =
      word.from < word.to ||
      context.explicit ||
      JEDI_TRIGGER.test(context.state.sliceDoc(lineStart, word.from))
    const typed = context.state.sliceDoc(word.from, word.to)
    const ownOptions = frappeOptions.filter((option) => option.label.startsWith(typed))
    const known = new Set(ownOptions.map((option) => option.label))
    const jediOptions = isTriggered
      ? (await getJediOptions(context)).filter((option) => !known.has(option.label))
      : []
    const options = [...ownOptions, ...jediOptions]
    // Jedi already filtered and ordered its list, so CodeMirror keeps it as is and asks again on each key.
    return options.length ? { from: word.from, options, filter: false } : null
  }

  return { source }
}
