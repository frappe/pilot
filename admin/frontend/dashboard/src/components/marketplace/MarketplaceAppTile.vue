<script setup lang="ts">
import { Badge, Button, Popover, Tooltip } from 'frappe-ui'
import { computed, ref } from 'vue'

import AppIcon from '@/components/apps/AppIcon.vue'
import GithubMark from '@/components/icons/GithubMark.vue'

interface TileApp {
  name: string
  title: string
  description?: string
  logo_url?: string | null
  repo?: string
  documentation?: string
  website?: string
  category?: string
  categories?: string[]
  frappe_version?: string
  label?: string
  stars?: number | null
  nightly?: boolean
  requires?: { name: string; title: string; logo_url: string }[]
  installed?: boolean
  compatible?: boolean
}

interface Props {
  app: TileApp
}

const props = defineProps<Props>()

defineEmits(['install'])

const compactNumber = new Intl.NumberFormat('en', { notation: 'compact' })
const isOpen = ref(false)

const hasMeta = computed(() => props.app.stars || props.app.label || props.app.requires?.length)

const categories = computed(() =>
  (props.app.categories?.length ? props.app.categories : [props.app.category])
    .filter(Boolean)
    .join(', '),
)

const status = computed(() => {
  if (props.app.installed) return 'Installed'
  if (props.app.compatible === false) return 'Needs a newer Frappe version'

  return 'Ready to install'
})

const statusTheme = computed(() => {
  if (props.app.installed) return 'gray'
  if (props.app.compatible === false) return 'amber'

  return 'blue'
})
</script>

<template>
  <Popover v-model:open="isOpen" trigger="manual" side="bottom" align="start" :offset="8">
    <template #trigger>
      <article
        class="relative flex flex-col dark:bg-surface-gray-0 p-4 border rounded-6 transition-colors"
        :class="isOpen ? 'border-outline-gray-5' : 'border-outline-gray-1.5'"
      >
        <div class="flex items-center gap-3">
          <AppIcon
            :name="app.name"
            :label="app.title"
            :logo="app.logo_url || ''"
            size="md"
            class="shrink-0"
          />

          <h3 class="flex-1 min-w-0 text-lg-semibold text-ink-gray-8">
            <button
              type="button"
              class="after:absolute after:inset-0 max-w-full after:rounded-6 text-left truncate focus-visible:outline-none focus-visible:after:focus-ring"
              :aria-expanded="isOpen"
              @click="isOpen = !isOpen"
            >
              {{ app.title }}
            </button>
          </h3>

          <Tooltip v-if="app.installed" text="Installed">
            <span
              class="size-4 text-ink-green-7 lucide-circle-check"
              role="img"
              aria-label="Installed"
            />
          </Tooltip>

          <Tooltip v-else-if="app.compatible === false" text="Needs a newer Frappe version">
            <span
              class="size-4 text-ink-amber-6 lucide-triangle-alert"
              role="img"
              aria-label="Needs a newer Frappe version"
            />
          </Tooltip>

          <Button
            v-else
            size="xs"
            variant="ghost"
            icon="lucide-download"
            :tooltip="`Install ${app.title}`"
            :aria-label="`Install ${app.title}`"
            class="z-10 relative -mr-1"
            @click="$emit('install', app)"
          />
        </div>

        <p class="mt-3 text-ink-gray-6 text-sm truncate">
          {{ app.description || 'No description yet.' }}
        </p>

        <div v-if="hasMeta" class="flex items-center gap-3 mt-auto pt-2 h-7 text-ink-gray-5 text-xs">
          <span v-if="app.stars" class="flex items-center gap-1 shrink-0">
            <span class="size-3 lucide-star" />
            {{ compactNumber.format(app.stars) }}
          </span>

          <span v-if="app.label" class="shrink-0">{{ app.label }}</span>

          <Tooltip
            v-for="dependency in app.requires"
            :key="dependency.name"
            :text="`Needs ${dependency.title}`"
          >
            <Badge :label="dependency.title" class="z-10 relative" />
          </Tooltip>
        </div>
      </article>
    </template>

    <template #default>
      <div class="flex flex-col gap-3 p-4 w-80 sm:w-96">
        <div class="flex justify-between items-start gap-3">
          <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
            {{ app.title }}
            <Badge v-if="app.nightly" label="Nightly" theme="amber" size="sm" />
          </p>

          <Badge :label="status" :theme="statusTheme" size="sm" class="shrink-0" />
        </div>

        <p class="-mt-1.5 text-p-sm text-ink-gray-6">
          {{ app.description || 'No description yet.' }}
        </p>

        <dl class="gap-x-4 gap-y-3 grid grid-cols-2 pt-3 border-t border-outline-gray-2">
          <div v-if="categories" class="flex items-center gap-2.5">
            <span
              class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
            >
              <span class="lucide-shapes size-4" aria-hidden="true" />
            </span>

            <div class="flex flex-col gap-1 min-w-0">
              <dt class="text-xs text-ink-gray-5">Category</dt>
              <dd class="text-sm text-ink-gray-8 truncate">{{ categories }}</dd>
            </div>
          </div>

          <div v-if="app.stars" class="flex items-center gap-2.5">
            <span
              class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
            >
              <span class="lucide-star size-4" aria-hidden="true" />
            </span>

            <div class="flex flex-col gap-1 min-w-0">
              <dt class="text-xs text-ink-gray-5">Stars</dt>
              <dd class="text-sm text-ink-gray-8 truncate">
                {{ compactNumber.format(app.stars) }}
              </dd>
            </div>
          </div>

          <div v-if="app.label" class="flex items-center gap-2.5">
            <span
              class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
            >
              <span class="lucide-git-branch size-4" aria-hidden="true" />
            </span>

            <div class="flex flex-col gap-1 min-w-0">
              <dt class="text-xs text-ink-gray-5">Version</dt>
              <dd class="text-sm text-ink-gray-8 truncate">{{ app.label }}</dd>
            </div>
          </div>

          <div v-if="app.frappe_version" class="flex items-center gap-2.5">
            <span
              class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
            >
              <span class="lucide-layers size-4" aria-hidden="true" />
            </span>

            <div class="flex flex-col gap-1 min-w-0">
              <dt class="text-xs text-ink-gray-5">Frappe</dt>
              <dd class="text-sm text-ink-gray-8 truncate">{{ app.frappe_version }}</dd>
            </div>
          </div>

          <div v-if="app.requires?.length" class="flex items-center gap-2.5 col-span-2">
            <span
              class="grid place-items-center bg-surface-gray-3 rounded-4 size-8 shrink-0 text-ink-gray-5"
            >
              <span class="lucide-package size-4" aria-hidden="true" />
            </span>

            <div class="flex flex-col gap-1 min-w-0">
              <dt class="text-xs text-ink-gray-5">Dependencies</dt>

              <dd class="flex flex-wrap gap-1">
                <Badge
                  v-for="dependency in app.requires"
                  :key="dependency.name"
                  :label="dependency.title"
                />
              </dd>
            </div>
          </div>
        </dl>

        <div v-if="app.repo || app.documentation || app.website" class="flex gap-2">
          <Button v-if="app.repo" :href="app.repo" class="flex-1" label="GitHub">
            <template #prefix><GithubMark class="size-4" /></template>
          </Button>

          <Button
            v-if="app.documentation || app.website"
            :href="app.documentation || app.website"
            class="flex-1"
            icon-left="lucide-external-link"
            label="Docs"
          />
        </div>
      </div>
    </template>
  </Popover>
</template>
