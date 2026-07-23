import { request, body } from './client'

export const searchApi = {
  find: ({ q, regex, caseSensitive, word }) =>
    request
      .get('search', {
        searchParams: {
          q,
          ...(caseSensitive ? { case: 1 } : {}),
          ...(word ? { word: 1 } : {}),
          ...(regex ? { regex: 1 } : {}),
        },
      })
      .then((r) => body(r, [])),

  replace: (payload) => request.post('replace', { json: payload }).then((r) => body(r, {})),
}
