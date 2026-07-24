import { request } from './client'

export const gitApi = {
  status: () => request.get('git/connection').json(),
  connect: (provider, token, username) =>
    request.put('git/connection', { json: { provider, token, username } }).json(),
  disconnect: () => request.delete('git/connection'),
  repos: () => request.get('git/repositories').json(),
  branches: (repo) => request.get('git/branches', { searchParams: { repo } }).json(),
  resolve: (repo, branch) =>
    request.post('git/repository-resolutions', { json: { repo, branch: branch || '' } }).json(),
}
