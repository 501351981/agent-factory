import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { buildSkillPrefixedMessage } from './skillMessage.ts'

describe('buildSkillPrefixedMessage', () => {
  it('returns text unchanged when no skills', () => {
    assert.equal(buildSkillPrefixedMessage('hello', []), 'hello')
  })

  it('prefixes skill_load instruction so timeline can show the tool call', () => {
    const out = buildSkillPrefixedMessage('看看当前目录', ['workspace-scout'])
    assert.match(out, /skill_load\(name="workspace-scout"\)/)
    assert.match(out, /看看当前目录/)
    assert.ok(out.indexOf('skill_load') < out.indexOf('看看当前目录'))
  })

  it('dedupes skill names and supports empty body', () => {
    const out = buildSkillPrefixedMessage('', ['stock-quote', 'stock-quote'])
    assert.match(out, /skill_load\(name="stock-quote"\)/)
    assert.equal((out.match(/skill_load\(/g) || []).length, 1)
  })
})

