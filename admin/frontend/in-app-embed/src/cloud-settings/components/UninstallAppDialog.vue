<script setup lang="ts">
import { Button, Dialog } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

interface Props {
  app?: Record<string, any> | null
  canDisable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  app: null,
})
const emit = defineEmits(['confirm'])
const open = defineModel({ type: Boolean, default: false })

const mode = ref('uninstall')

watch(open, (isOpen) => {
  if (isOpen) mode.value = props.canDisable ? 'disable' : 'uninstall'
})

const title = computed(() => props.app?.title || props.app?.name || '')

const options = computed(() => [
  {
    value: 'disable',
    label: __('Disable'),
    icon: 'lucide-circle-slash',
    description: __(
      '{0} stops working on the site. Its data stays, so installing it again brings it back.',
      [title.value],
    ),
  },
  {
    value: 'uninstall',
    label: __('Uninstall'),
    icon: 'lucide-trash-2',
    description: __('{0} stops working, and all of its data is deleted.', [title.value]),
  },
])

const confirm = () => {
  open.value = false

  emit('confirm', props.app, mode.value)
}
</script>

<template>
  <Dialog
    v-model="open"
    :title="canDisable ? __('Remove {0}', [title]) : __('Uninstall {0}', [title])"
    size="md"
  >
    <template #default>
      <div v-if="canDisable" class="mb-4 grid gap-1.5">
        <button
          v-for="option in options"
          :key="option.value"
          type="button"
          class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 rounded-4 p-2.5 text-left"
          :class="mode === option.value ? 'bg-surface-gray-3' : 'hover:bg-surface-gray-2'"
          @click="mode = option.value"
        >
          <span :class="[option.icon, 'row-span-2 mt-px size-4 text-ink-gray-6']" />
          <span class="text-base-medium text-ink-gray-8">{{ option.label }}</span>
          <span class="mt-1 text-p-sm text-ink-gray-5">{{ option.description }}</span>
        </button>
      </div>

      <div
        v-if="mode === 'uninstall'"
        class="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 rounded-6 border border-outline-red-2 bg-surface-red-1 p-3"
        role="alert"
      >
        <span
          class="lucide-triangle-alert row-span-2 mt-0.5 size-4 shrink-0 text-ink-red-8"
          aria-hidden="true"
        />

        <p class="text-sm-medium text-ink-red-8">{{ __("This can't be undone.") }}</p>

        <p class="mt-1 text-p-sm text-ink-red-8">
          {{ __("Everything {0} stores on this site is deleted. Back the site up first if you need the data.", [title]) }}
        </p>
      </div>
    </template>

    <template #actions>
      <div class="flex justify-end gap-2 -mt-4">
        <Button :label="__('Cancel')" @click="open = false" />

        <Button
          variant="solid"
          :theme="mode === 'uninstall' ? 'red' : 'gray'"
          :label="mode === 'uninstall' ? __('Uninstall') : __('Disable')"
          @click="confirm"
        />
      </div>
    </template>
  </Dialog>
</template>
