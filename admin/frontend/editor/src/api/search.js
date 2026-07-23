import { request, body } from './client'

export const searchApi = {
  // opts is the { case, word, regex } shape the search UI keeps in state.
  find: (q, opts) =>
    request
      .get('search', {
        searchParams: {
          q,
          ...(opts.case ? { case: 1 } : {}),
          ...(opts.word ? { word: 1 } : {}),
          ...(opts.regex ? { regex: 1 } : {}),
        },
      })
      .then((r) => body(r, [])),

  replace: (payload) => request.post('replace', { json: payload }).then((r) => body(r, {})),
}
