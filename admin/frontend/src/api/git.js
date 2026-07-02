import { request } from './client'

export const gitApi = {
  status: () => request.get('git/integration').json(),
  connect: (provider, token, username) =>
    request.post('git/integration', { json: { provider, token, username } }).json(),
  disconnect: () => request.delete('git/integration').json(),
  repos: () => request.get('git/repos').json(),
  branches: (repo) => request.get('git/branches', { searchParams: { repo } }).json(),
  resolve: (repo, branch) => request.get('git/resolve', { searchParams: { repo, branch: branch || '' } }).json(),
}
