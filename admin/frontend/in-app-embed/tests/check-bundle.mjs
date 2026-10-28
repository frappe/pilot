import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

/** Check the ES runtime the SDK imports: its exports, inlined styles, and lazy panels. */
export const checkRuntimeBundle = async (directory) => {
  const source = await readFile(path.join(directory, 'cloud-settings.js'), 'utf8')
  const exported = source.match(/export\s*\{([^}]*)\}/)?.[1] ?? ''

  assert.match(exported, /\bmountCloudSettings\b/, 'The runtime must export its mount method')
  assert.match(exported, /\bcloseCloudSettings\b/, 'The runtime must export its close method')
  assert.ok(!source.includes('__CLOUD_SETTINGS_STYLES__'), 'The runtime must contain its styles')
  assert.ok(!source.includes('process.env.NODE_ENV'), 'The browser runtime must not require Node globals')
  assert.ok(source.includes('--surface-gray-2'), 'The runtime must contain Espresso colors')
  assert.ok(!source.includes('Your plan, credit and payment method.'), 'Panels must load from their own chunks, not the runtime')
  assert.match(source, /import\("\.\/chunks\/BillingPanel-[\w-]+\.js"\)/, 'The billing panel must load on demand')
  console.log('Cloud Settings runtime exports, styles and lazy panels verified')
}
