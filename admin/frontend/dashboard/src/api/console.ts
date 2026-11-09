import { request, unwrap } from '@/api/client'
import type { ConsoleCompletion, PythonDocstring } from '@/types/console'

export const consoleApi = {
  /** Jedi completions at a 1-based line and 0-based column. */
  completions: (
    code: string,
    line: number,
    column: number,
    controllers: Record<string, string>,
  ): Promise<ConsoleCompletion[]> =>
    unwrap(
      request.post('console/completions', { json: { code, line, column, controllers } }).json(),
    ),

  docstring: (
    code: string,
    line: number,
    column: number,
    controllers: Record<string, string>,
    name: string,
  ): Promise<PythonDocstring> =>
    unwrap(
      request.post('console/docstring', { json: { code, line, column, controllers, name } }).json(),
    ),
}
