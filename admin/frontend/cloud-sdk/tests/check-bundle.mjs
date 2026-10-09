import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../dist/embed/cloud-settings.js', import.meta.url), 'utf8')
const parsed = ts.createSourceFile('cloud-settings.js', source, ts.ScriptTarget.Latest, true)
const exports = parsed.statements.filter(ts.isExportDeclaration)
  .flatMap((statement) => statement.exportClause?.elements?.map((element) => element.name.text) ?? [])

assert.ok(exports.includes('mountCloudSettings'), 'The packaged runtime must export its mount method')
assert.ok(exports.includes('closeCloudSettings'), 'The packaged runtime must export its close method')
assert.ok(!source.includes('__CLOUD_SETTINGS_STYLES__'), 'The runtime must contain its styles')
assert.ok(!source.includes('process.env.NODE_ENV'), 'The browser runtime must not require Node globals')
assert.ok(source.includes('--surface-gray-2'), 'The runtime must contain Espresso colors')
console.log('Packaged UI exports and styles verified')
