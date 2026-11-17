import {
  type Language,
  LanguageSupport,
  StreamLanguage,
  type StreamParser,
} from '@codemirror/language'
import type { Extension } from '@codemirror/state'

import { syntaxErrors } from '@/codemirror/setup'

type Loaded = Language | LanguageSupport

export interface LoadedLanguage {
  language: Language
  extension: Extension
}

const legacy =
  <T>(load: () => Promise<StreamParser<T>>) =>
  async () =>
    StreamLanguage.define(await load())

const loaders: Record<string, () => Promise<Loaded>> = {
  javascript: () => import('@codemirror/lang-javascript').then((m) => m.javascript({ jsx: true })),
  typescript: () =>
    import('@codemirror/lang-javascript').then((m) =>
      m.javascript({ jsx: true, typescript: true }),
    ),
  python: () => import('@codemirror/lang-python').then((m) => m.python()),
  vue: () => import('@codemirror/lang-vue').then((m) => m.vue()),
  html: () => import('@codemirror/lang-html').then((m) => m.html()),
  css: () => import('@codemirror/lang-css').then((m) => m.css()),
  scss: () => import('@codemirror/lang-sass').then((m) => m.sass()),
  json: () => import('@codemirror/lang-json').then((m) => m.json()),
  markdown: () => import('@codemirror/lang-markdown').then((m) => m.markdown()),
  yaml: () => import('@codemirror/lang-yaml').then((m) => m.yaml()),
  xml: () => import('@codemirror/lang-xml').then((m) => m.xml()),
  sql: () => import('@codemirror/lang-sql').then((m) => m.sql()),
  shell: legacy(() => import('@codemirror/legacy-modes/mode/shell').then((m) => m.shell)),
  toml: legacy(() => import('@codemirror/legacy-modes/mode/toml').then((m) => m.toml)),
}

const extensions: Record<string, string> = {
  js: 'javascript',
  cjs: 'javascript',
  mjs: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  vue: 'vue',
  html: 'html',
  htm: 'html',
  css: 'css',
  scss: 'scss',
  json: 'json',
  md: 'markdown',
  yml: 'yaml',
  yaml: 'yaml',
  xml: 'xml',
  sql: 'sql',
  sh: 'shell',
  bash: 'shell',
  toml: 'toml',
}

const checked = new Set(['javascript', 'typescript', 'json', 'css', 'scss'])

const cache = new Map<string, Promise<LoadedLanguage>>()

const languageName = (path: string) => {
  const name = path.toLowerCase()
  const dot = name.lastIndexOf('.')

  return dot === -1 ? '' : (extensions[name.slice(dot + 1)] ?? '')
}

const load = async (name: string): Promise<LoadedLanguage> => {
  const loaded = await loaders[name]()
  const language = loaded instanceof LanguageSupport ? loaded.language : loaded

  return { language, extension: checked.has(name) ? [loaded, syntaxErrors] : loaded }
}

export const loadLanguage = async (path: string) => {
  const name = languageName(path)

  if (!loaders[name]) return null

  if (!cache.has(name)) cache.set(name, load(name))

  return cache.get(name) ?? null
}
