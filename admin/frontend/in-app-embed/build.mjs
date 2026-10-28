import path from 'node:path'
import { parseArgs } from 'node:util'
import { build } from 'vite'
import { checkRuntimeBundle } from './tests/check-bundle.mjs'

// Pilot passes `--outDir <staging>`; both outputs must land under it.
const { values } = parseArgs({ options: { outDir: { type: 'string' } }, strict: false })
const outDir = values.outDir && path.resolve(values.outDir)

await build({ build: outDir ? { outDir } : {} })
await build({ mode: 'esm', build: outDir ? { outDir: path.join(outDir, 'esm') } : {} })

await checkRuntimeBundle(outDir ? path.join(outDir, 'esm') : path.resolve('../../backend/static/in-app-embed/cloud-settings/esm'))
