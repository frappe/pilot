<script setup lang="ts">
import { Badge, Button, Dropdown, Tooltip } from 'frappe-ui'
import { computed, inject, ref } from 'vue'

interface Props {
  app: Record<string, any>
  pending?: string
  error?: string
}

const props = withDefaults(defineProps<Props>(), {
  pending: '',
  error: '',
})
const emit = defineEmits(['install', 'uninstall', 'update'])

const busy = computed(() => Boolean(props.pending))
const imageFailed = ref(false)
const overlayTarget = inject('overlayTarget', 'body')

const logoUrl = computed(() => {
  const url = String(props.app.logo_url || '').trim()

  return /^https?:\/\//i.test(url) && !imageFailed.value ? url : ''
})

const requiredVersion = computed(() => String(props.app.required_version || '').match(/\d+/)?.[0])
const incompatibleLabel = computed(() =>
  requiredVersion.value ? __('Needs Version {0}', [requiredVersion.value]) : __('Incompatible'),
)
const incompatibleReason = computed(() =>
  requiredVersion.value
    ? __("Needs Version {0} — change your server's version to install it", [requiredVersion.value])
    : __('Not available for this version of Frappe'),
)
</script>

<template>
  <div class="grid grid-cols-[auto_minmax(0,max-content)_1fr_auto] items-center gap-x-1.5 py-2">
    <img
      v-if="logoUrl"
      class="row-span-2 mr-1.5 size-10 rounded-6 object-cover"
      :src="logoUrl"
      :alt="app.title"
      loading="lazy"
      decoding="async"
      @error="imageFailed = true"
    />

    <div
      v-else
      class="row-span-2 mr-1.5 grid size-10 place-items-center rounded-6 bg-surface-gray-2 text-base-medium uppercase text-ink-gray-6"
    >
      {{ (app.title || "?").charAt(0) }}
    </div>

    <span class="truncate text-base-medium text-ink-gray-8" :title="app.title">
      {{ app.title }}
    </span>

    <span class="whitespace-nowrap text-sm tabular-nums text-ink-gray-5">
      <template v-if="app.installed && app.has_update">
        v{{ app.installed_version }}
        <span class="text-ink-green-7">→ v{{ app.latest_version }}</span>
      </template>

      <template v-else-if="app.installed && app.installed_version">
        v{{ app.installed_version }}
      </template>

      <template v-else-if="app.latest_version">v{{ app.latest_version }}</template>
    </span>

    <p
      class="col-span-2 col-start-2 mt-0.5 truncate text-p-sm text-ink-gray-5"
      :title="app.description"
    >
      {{ app.description }}
    </p>

    <div class="col-start-4 row-span-2 row-start-1 flex items-center gap-1">
      <Tooltip v-if="error && !busy" :text="error">
        <span
          class="lucide-triangle-alert size-3.5 text-ink-red-8"
          role="img"
          tabindex="0"
          :aria-label="error"
        />
      </Tooltip>

      <Tooltip v-if="!app.installed && !app.installable" :text="incompatibleReason">
        <Badge size="sm" :label="incompatibleLabel" />
      </Tooltip>

      <Tooltip v-else-if="!app.installed" :text="__('Install {0}', [app.title])">
        <Button
          variant="ghost"
          class="group"
          :loading="pending === 'install'"
          :label="__('Install')"
          @click="emit('install', app)"
        >
          <template #icon>
            <span
              class="lucide-download size-4 transition-transform duration-150 group-active:scale-95"
            />
          </template>
        </Button>
      </Tooltip>

      <Button
        v-else-if="app.has_update"
        variant="ghost"
        :loading="pending === 'update'"
        :label="pending === 'update' ? __('Updating') : __('Update')"
        @click="emit('update', app)"
      />

      <Dropdown
        v-if="app.installed"
        align="end"
        :portal-to="overlayTarget"
        :options="[
          {
            label: __('Uninstall'),
            icon: 'lucide-trash-2',
            onClick: () => emit('uninstall', app),
          },
        ]"
      >
        <Button
          variant="ghost"
          icon="lucide-ellipsis-vertical"
          :disabled="pending === 'update'"
          :loading="pending === 'uninstall' || pending === 'disable'"
          :label="__('More actions for {0}', [app.title])"
        />
      </Dropdown>
    </div>
  </div>
</template>
