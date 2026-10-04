import { request } from '@/api/client'
import type { GitBranches, GitConnection, GitRepository, ResolvedApp } from '@/types/git'

export const gitApi = {
  status: (): Promise<GitConnection> => request.get('git/connection').json(),

  connect: (provider: string, token: string, username: string): Promise<GitConnection> =>
    request.put('git/connection', { json: { provider, token, username } }).json(),

  disconnect: () => request.delete('git/connection'),
  repos: (): Promise<GitRepository[]> => request.get('git/repositories').json(),

  branches: (repo: string): Promise<GitBranches> =>
    request.get('git/branches', { searchParams: { repo } }).json(),

  resolve: (repo: string, branch?: string): Promise<ResolvedApp> =>
    request.post('git/repository-resolutions', { json: { repo, branch: branch || '' } }).json(),
}
