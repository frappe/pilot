<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Dialog } from 'frappe-ui'

interface Props {
  process?: any
}

const props = withDefaults(defineProps<Props>(), { process: null })

const open = defineModel('open')

const declaration = computed(() => props.process?.declaration || {})

const fields = computed(() => [
  { label: 'Working directory', value: declaration.value.working_dir || '—' },
  {
    label: 'Restart on failure',
    value: declaration.value.restart_on_failure ? 'Yes' : 'No',
  },
  {
    label: 'Stop timeout',
    value: declaration.value.stop_timeout ? `${declaration.value.stop_timeout}s` : 'Manager default',
  },
])
</script>

<template>
  <Dialog v-model="open" :title="process?.name || 'Declaration'" size="lg">
    <div class="space-y-4">
      <p class="text-ink-gray-6 text-p-sm">
        Declared by the app in its <span class="font-mono text-ink-gray-7">pyproject.toml</span>.
      </p>

      <div>
        <p class="mb-1.5 font-medium text-ink-gray-7 text-sm">Command</p>
        <pre
          class="bg-surface-gray-2 p-3 rounded-6 overflow-x-auto font-mono text-ink-gray-8 text-xs leading-5"
        >{{ declaration.cmd }}</pre>
      </div>

      <dl class="gap-x-4 grid grid-cols-[auto_1fr] text-p-sm">
        <template v-for="field in fields" :key="field.label">
          <dt class="py-1.5 text-ink-gray-5">{{ field.label }}</dt>
          <dd class="py-1.5 text-ink-gray-8 text-right tabular-nums">{{ field.value }}</dd>
        </template>
      </dl>

      <div v-if="declaration.has_hooks" class="flex items-start gap-3 bg-surface-amber-1 p-3 border border-outline-amber-2 rounded-6">
        <span class="mt-0.5 size-4 text-ink-amber-3 lucide-terminal shrink-0" />
        <div class="min-w-0 text-p-sm text-ink-gray-7">
          <p class="font-medium text-ink-gray-8">Runs setup hooks</p>
          <p class="mt-0.5 leading-5">
            A pre-run hook runs on every start. If it fails, the process never comes up - its log
            is the place to look.
          </p>
        </div>
      </div>
    </div>
  </Dialog>
</template>
