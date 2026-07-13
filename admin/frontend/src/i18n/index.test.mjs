import assert from 'node:assert/strict'

import { defaultLanguage, messages } from './messages.js'

const languages = Object.keys(messages)
const requiredKeys = Object.keys(messages[defaultLanguage])

for (const language of languages) {
  assert.deepEqual(
    Object.keys(messages[language]).sort(),
    requiredKeys.sort(),
    `${language} must define every translation key`,
  )
}

assert.equal(messages['zh-CN']['login.signIn'], '登录')
assert.equal(messages.en['navigation.sites'], 'Sites')
