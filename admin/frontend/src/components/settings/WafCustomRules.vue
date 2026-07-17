<template>
  <div class="space-y-4">
    <div class="flex justify-between items-center">
      <div>
        <p class="font-medium text-ink-gray-8 text-base leading-normal">Custom rules</p>
        <p class="text-ink-gray-5 text-xs">Match requests and block, log, or skip the WAF. Evaluated before the managed
          rules, top to bottom.</p>
      </div>
      <Button variant="subtle" icon-left="plus" @click="addRule">Add rule</Button>
    </div>

    <div v-if="!rules.length"
      class="flex flex-col items-center gap-2.5 py-10 border border-dashed rounded-lg border-outline-gray-2 text-center">
      <div class="flex justify-center items-center bg-surface-gray-2 rounded-full size-11">
        <span class="size-5 text-ink-gray-5 lucide-list-filter"></span>
      </div>
      <p class="font-medium text-ink-gray-7 text-sm">No custom rules</p>
      <p class="max-w-xs text-ink-gray-5 text-xs">Add a rule to block or log requests by path, IP, method, header, and more.</p>
    </div>

    <div v-for="(rule, ri) in rules" :key="ri"
      class="space-y-3 bg-surface-gray-1 p-4 border rounded-lg border-outline-gray-2">
      <div class="flex items-center gap-2">
        <TextInput v-model="rule.name" placeholder="Rule name" class="flex-1" />
        <Switch :model-value="rule.enabled" @update:model-value="(v) => (rule.enabled = v)" />
        <Button variant="ghost" icon="lucide-trash-2" @click="removeRule(ri)" />
      </div>

      <div class="flex flex-wrap items-center gap-2 text-ink-gray-7 text-sm">
        <span>When</span>
        <Select v-model="rule.match" :options="MATCH_OPTIONS" class="w-24" />
        <span>of the following match:</span>
      </div>

      <div v-for="(cond, ci) in rule.conditions" :key="ci" class="flex flex-wrap items-center gap-2">
        <Select v-model="cond.field" :options="fieldOptions" class="w-40" />
        <TextInput v-if="cond.field === 'header'" v-model="cond.header_name" placeholder="Header name" class="w-36" />
        <Select v-model="cond.operator" :options="operatorOptions" class="w-44" />
        <TextInput v-model="cond.value" :placeholder="placeholder(cond.field)" class="flex-1 min-w-40" />
        <Button variant="ghost" icon="lucide-x" @click="removeCondition(rule, ci)" />
      </div>
      <Button variant="ghost" icon-left="plus" @click="addCondition(rule)">Add condition</Button>

      <div class="flex items-center gap-2 text-ink-gray-7 text-sm">
        <span>Then</span>
        <Select v-model="rule.action" :options="actionOptions" class="w-48" />
      </div>

      <p class="text-ink-gray-5 text-xs">{{ preview(rule) }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Button, Select, Switch, TextInput } from 'frappe-ui'

const props = defineProps({
  rules: { type: Array, required: true },
  fields: { type: Array, default: () => [] },
  operators: { type: Array, default: () => [] },
  actions: { type: Array, default: () => [] },
})

const FIELD_LABELS = {
  uri_path: 'URI Path', uri_full: 'Full URI', query: 'Query String', method: 'HTTP Method',
  source_ip: 'Source IP', user_agent: 'User Agent', header: 'Request Header', host: 'Host',
}
const OPERATOR_LABELS = {
  is: 'is', is_not: 'is not', contains: 'contains', not_contains: 'does not contain',
  starts_with: 'starts with', matches: 'matches regex',
}
const ACTION_LABELS = { block: 'Block', log: 'Log', skip: 'Skip (bypass WAF)' }
const PLACEHOLDERS = {
  source_ip: '10.0.0.0/8, 203.0.113.4', method: 'POST', uri_path: '/admin', host: 'example.com',
}
const MATCH_OPTIONS = [{ label: 'All', value: 'all' }, { label: 'Any', value: 'any' }]

const fieldOptions = computed(() => props.fields.map((f) => ({ label: FIELD_LABELS[f] || f, value: f })))
const operatorOptions = computed(() => props.operators.map((o) => ({ label: OPERATOR_LABELS[o] || o, value: o })))
const actionOptions = computed(() => props.actions.map((a) => ({ label: ACTION_LABELS[a] || a, value: a })))

function placeholder(field) {
  return PLACEHOLDERS[field] || 'value'
}

function newCondition() {
  return { field: 'uri_path', operator: 'contains', value: '', header_name: '' }
}
function addRule() {
  props.rules.push({ name: '', action: 'block', match: 'all', enabled: true, conditions: [newCondition()] })
}
function removeRule(index) {
  props.rules.splice(index, 1)
}
function addCondition(rule) {
  rule.conditions.push(newCondition())
}
function removeCondition(rule, index) {
  rule.conditions.splice(index, 1)
}

function preview(rule) {
  const joiner = rule.match === 'any' ? ' OR ' : ' AND '
  const parts = rule.conditions.map((c) => {
    const field = c.field === 'header' ? `Header ${c.header_name || '?'}` : (FIELD_LABELS[c.field] || c.field)
    return `${field} ${OPERATOR_LABELS[c.operator] || c.operator} "${c.value || '…'}"`
  })
  return `When ${parts.join(joiner) || '…'} → ${ACTION_LABELS[rule.action] || rule.action}`
}
</script>
