<script setup lang="ts">
import { Button, Checkbox, Dialog, ErrorMessage } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

interface Props {
  apps?: Record<string, any>[]
  updating?: boolean
  error?: string
}

const props = withDefaults(defineProps<Props>(), {
  apps: () => [],
  error: '',
})
const emit = defineEmits(['submit'])
const open = defineModel({ type: Boolean, default: false })

const selected = ref(new Set())

watch(
  open,
  (isOpen) => {
    if (!isOpen) return

    selected.value = new Set(props.apps.map((app) => app.name))
  },
  { immediate: true },
)

const submitLabel = computed(() => {
  if (props.updating) return __('Updating')
  if (!selected.value.size) return __('Update')
  if (selected.value.size === props.apps.length) return __('Update all')
  if (selected.value.size === 1) return __('Update 1 app')

  return __('Update {0} apps', [selected.value.size])
})

const toggle = (name) => {
  const next = new Set(selected.value)

  next.has(name) ? next.delete(name) : next.add(name)

  selected.value = next
}

const submit = () => {
  if (!selected.value.size || props.updating) return

  emit('submit', { apps: [...selected.value] })
}
</script>

<template>
  <Dialog v-model="open" :title="__('Updates')" size="md" :dismissible="!updating">
    <template #default>
      <div class="max-h-60 space-y-1 overflow-y-auto">
        <button
          v-for="app in apps"
          :key="app.name"
          type="button"
          class="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 rounded-5 p-2 text-left hover:bg-surface-gray-2 disabled:cursor-not-allowed"
          :disabled="updating"
          @click="toggle(app.name)"
        >
          <img
            v-if="/^https?:\/\//i.test(app.logo_url || '')"
            class="row-span-2 size-8 rounded-5 object-cover"
            :src="app.logo_url"
            :alt="app.title"
          />

          <span
            v-else
            class="row-span-2 flex size-8 items-center justify-center rounded-5 bg-surface-gray-2 text-sm-medium uppercase text-ink-gray-6"
          >
            {{ (app.title || "?").charAt(0) }}
          </span>

          <span class="truncate text-base-medium text-ink-gray-8">
            {{ app.title }}
          </span>

          <span class="col-start-2 text-p-sm tabular-nums text-ink-gray-5">
            v{{ app.installed_version }}
            <span class="px-1">→</span>
            <span class="text-ink-green-7">v{{ app.latest_version }}</span>
          </span>

          <Checkbox
            :model-value="selected.has(app.name)"
            :disabled="updating"
            class="pointer-events-none col-start-3 row-span-2 row-start-1"
            :aria-label="app.title"
          />
        </button>
      </div>

      <div v-if="updating" class="mt-4 text-p-sm text-ink-gray-5" role="status">
        {{ __("Updating selected apps. This can take a few minutes.") }}
      </div>

      <ErrorMessage :message="error" class="mt-4" />
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :disabled="updating || !selected.size" @click="selected = new Set()">
          {{ __("Clear") }}
        </Button>

        <Button variant="solid" :disabled="updating || !selected.size" @click="submit">
          {{ submitLabel }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
