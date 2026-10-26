<script setup lang="ts">
import { useTranslation } from '../translation'
import { Badge, Button, Dropdown, HoverCard, Tooltip } from 'frappe-ui'
import { computed, inject, ref } from 'vue'
import { openExternal } from '../external'
import GithubIcon from './GithubIcon.vue'

const __ = useTranslation()

interface Props {
  app: Record<string, any>
  appTitles?: Record<string, string>
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
const isDetailsOpen = ref(false)
const isOverActions = ref(false)

const logoUrl = computed(() => {
  const url = String(props.app.logo_url || '').trim()

  return /^https?:\/\//i.test(url) && !imageFailed.value ? url : ''
})

const compactNumber = new Intl.NumberFormat('en', { notation: 'compact' })

const requiredVersion = computed(() => String(props.app.required_version || '').match(/\d+/)?.[0])
const incompatibleLabel = computed(() =>
  requiredVersion.value ? __('Needs Version {0}', [requiredVersion.value]) : __('Incompatible'),
)
const incompatibleReason = computed(() =>
  requiredVersion.value
    ? __("Needs version {0}. Change your server's version to install it.", [requiredVersion.value])
    : __('Not available for this version of Frappe'),
)

const status = computed(() => {
  if (props.app.installed && props.app.has_update) return __('Update available')
  if (props.app.installed) return __('Installed')
  if (!props.app.installable) return incompatibleLabel.value

  return __('Ready to install')
})

const statusTheme = computed(() => {
  if (props.app.installed && props.app.has_update) return 'green'
  if (props.app.installed) return 'gray'
  if (!props.app.installable) return 'amber'

  return 'blue'
})
</script>

<template>
  <HoverCard
    :open="isDetailsOpen"
    side="bottom"
    align="start"
    :offset="4"
    :portal-to="overlayTarget"
    @update:open="(value) => (isDetailsOpen = value && !isOverActions)"
  >
    <template #trigger>
      <article
        class="flex flex-col px-3 py-2 rounded-6 hover:bg-surface-gray-1 transition-colors"
        :class="{ 'bg-surface-gray-1': isDetailsOpen }"
      >
        <div class="flex items-center gap-3">
          <img
            v-if="logoUrl"
            class="size-6 rounded-2 object-cover shrink-0"
            :src="logoUrl"
            :alt="app.title"
            loading="lazy"
            decoding="async"
            @error="imageFailed = true"
          />

          <div
            v-else
            class="grid place-items-center size-6 rounded-2 bg-surface-gray-2 text-xs-medium uppercase text-ink-gray-6 shrink-0"
          >
            {{ (app.title || "?").charAt(0) }}
          </div>

          <span class="flex-1 truncate text-base-semibold text-ink-gray-8" :title="app.title">
            {{ app.title }}
          </span>

          <div
            class="flex items-center gap-1 -mr-1 shrink-0"
            @pointerenter="(isOverActions = true), (isDetailsOpen = false)"
            @pointerleave="isOverActions = false"
          >
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
              size="sm"
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

        <p class="mt-1.5 truncate text-sm text-ink-gray-6">{{ app.description }}</p>

        <div class="flex items-center gap-3 mt-auto pt-2 text-xs tabular-nums text-ink-gray-5">
          <span v-if="app.stars" class="flex items-center gap-1">
            <span class="lucide-star size-3" />
            {{ compactNumber.format(app.stars) }}
          </span>

          <span v-if="app.installed && app.has_update">
            v{{ app.installed_version }}
            <span class="text-ink-green-7">→ v{{ app.latest_version }}</span>
          </span>

          <span v-else-if="app.installed && app.installed_version">v{{ app.installed_version }}</span>

          <span v-else-if="app.latest_version">v{{ app.latest_version }}</span>
        </div>
      </article>
    </template>

    <div class="flex flex-col gap-3 p-4 w-80 sm:w-96">
      <div class="flex justify-between items-start gap-3">
        <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
          {{ app.title }}
          <Badge v-if="app.nightly" :label="__('Nightly')" theme="amber" size="sm" />
        </p>

        <Badge :label="status" :theme="statusTheme" size="sm" class="shrink-0" />
      </div>

      <p class="-mt-1.5 text-p-sm text-ink-gray-6">{{ app.description }}</p>

      <dl class="gap-x-4 gap-y-3 grid grid-cols-2 pt-3 border-t border-outline-gray-2">
        <div v-if="app.categories?.length || app.category" class="flex items-center gap-2.5">
          <span
            class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
          >
            <span class="lucide-shapes size-4" aria-hidden="true" />
          </span>

          <div class="flex flex-col gap-1 min-w-0">
            <dt class="text-xs text-ink-gray-5">{{ __("Category") }}</dt>

            <dd class="text-sm text-ink-gray-8 truncate">
              {{ (app.categories?.length ? app.categories : [app.category]).join(", ") }}
            </dd>
          </div>
        </div>

        <div v-if="app.stars" class="flex items-center gap-2.5">
          <span
            class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
          >
            <span class="lucide-star size-4" aria-hidden="true" />
          </span>

          <div class="flex flex-col gap-1 min-w-0">
            <dt class="text-xs text-ink-gray-5">{{ __("Stars") }}</dt>

            <dd class="text-sm text-ink-gray-8 tabular-nums">
              {{ compactNumber.format(app.stars) }}
            </dd>
          </div>
        </div>

        <div class="flex items-center gap-2.5">
          <span
            class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
          >
            <span class="lucide-git-branch size-4" aria-hidden="true" />
          </span>

          <div class="flex flex-col gap-1 min-w-0">
            <dt class="text-xs text-ink-gray-5">{{ __("Version") }}</dt>

            <dd class="text-sm text-ink-gray-8 tabular-nums">
              <template v-if="app.installed && app.has_update">
                v{{ app.installed_version }}
                <span class="text-ink-green-7">→ v{{ app.latest_version }}</span>
              </template>

              <template v-else>v{{ app.installed_version || app.latest_version }}</template>
            </dd>
          </div>
        </div>

        <div v-if="app.frappe_version" class="flex items-center gap-2.5">
          <span
            class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
          >
            <span class="lucide-layers size-4" aria-hidden="true" />
          </span>

          <div class="flex flex-col gap-1 min-w-0">
            <dt class="text-xs text-ink-gray-5">{{ __("Frappe") }}</dt>
            <dd class="text-sm text-ink-gray-8 tabular-nums">{{ app.frappe_version }}</dd>
          </div>
        </div>

        <div v-if="app.dependencies?.length" class="flex items-center gap-2.5 col-span-2">
          <span
            class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
          >
            <span class="lucide-package size-4" aria-hidden="true" />
          </span>

          <div class="flex flex-col gap-1 min-w-0">
            <dt class="text-xs text-ink-gray-5">{{ __("Dependencies") }}</dt>

            <dd class="flex flex-wrap gap-1">
              <Badge
                v-for="dependency in app.dependencies"
                :key="dependency"
                :label="appTitles?.[dependency] || dependency"
              />
            </dd>
          </div>
        </div>
      </dl>

      <div v-if="app.repo || app.documentation || app.website" class="flex gap-2">
        <Button
          v-if="app.repo"
          class="flex-1"
          :label="__('GitHub')"
          @click="openExternal(app.repo)"
        >
          <template #prefix><GithubIcon class="size-4" /></template>
        </Button>

        <Button
          v-if="app.documentation || app.website"
          class="flex-1"
          icon-left="lucide-external-link"
          :label="__('Docs')"
          @click="openExternal(app.documentation || app.website)"
        />
      </div>
    </div>
  </HoverCard>
</template>
