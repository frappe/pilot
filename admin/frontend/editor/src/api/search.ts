import { body, request } from '@/api/client'

export interface SearchOptions {
  case: boolean
  word: boolean
  regex: boolean
}

export interface SearchMatch {
  line: number
  text: string
  start: number
  end: number
}

export interface SearchResult {
  file: string
  matches: SearchMatch[]
}

export interface ReplacePayload extends SearchOptions {
  query: string
  replace: string
  files: string[]
}

export const searchApi = {
  // opts is the { case, word, regex } shape the search UI keeps in state.
  find: (q: string, opts: SearchOptions) =>
    request
      .get('search', {
        searchParams: {
          q,
          ...(opts.case ? { case: 1 } : {}),
          ...(opts.word ? { word: 1 } : {}),
          ...(opts.regex ? { regex: 1 } : {}),
        },
      })
      // No fallback: a broken search must surface, not read as "no matches".
      .then((r) => body<SearchResult[]>(r)),

  replace: (payload: ReplacePayload) =>
    request.post('replace', { json: payload }).then((r) => body<{ changed?: string[] }>(r, {})),
}
