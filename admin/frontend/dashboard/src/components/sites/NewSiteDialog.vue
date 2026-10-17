<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, Select, Spinner, TextInput } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'

import { useNewSite } from '@/composables/sites/useNewSite'
import type { SiteResource } from '@/types/sites'

interface Props {
  sites?: SiteResource[]
}

withDefaults(defineProps<Props>(), {
  sites: () => [],
})

interface Emits {
  started: [taskId: string]
}

const emit = defineEmits<Emits>()
const open = defineModel<boolean>()

const {
  name: newSiteName,
  prefix: sitePrefix,
  wildcardDomains,
  suffix: selectedSuffix,
  selectedApps,
  appSearch,
  loading,
  creating,
  error,
  hasSingleDomain,
  availableApps,
  filteredApps,
  reset,
  toggleApp,
  create,
} = useNewSite()

// Fade only the edges that can still scroll, so a clipped row reads as "more
// below" and the last app is never dimmed at rest.
const appList = ref<HTMLElement | null>(null)
const fadeEdges = ref('')

const updateFadeEdges = () => {
  const el = appList.value
  if (!el) return
  const top = el.scrollTop > 1
  const bottom = Math.ceil(el.scrollTop + el.clientHeight) < el.scrollHeight - 1
  fadeEdges.value = top && bottom ? 'both' : top ? 'top' : bottom ? 'bottom' : ''
}

// Also `loading`: apps resolve while the spinner is still up, so the rows only
// exist once it flips.
watch([filteredApps, loading], () => nextTick(updateFadeEdges))

watch(open, (visible) => {
  if (visible) reset()
})

const submit = async () => {
  const taskId = await create()
  if (!taskId) return

  open.value = false
  emit('started', taskId)
}
</script>

<template>
  <Dialog v-model="open" title="New Site" size="2xl">
    <div v-if="loading" class="flex justify-center items-center h-80">
      <Spinner size="lg" class="text-ink-gray-4" />
    </div>

    <div v-else @pointerdown.stop class="space-y-5">
      <!-- Site name -->
      <!-- Without wildcard site name -->
      <TextInput
        v-if="!wildcardDomains.length"
        v-model="newSiteName"
        label="Site name"
        placeholder="mysite.localhost"
        @keyup.enter="submit"
      />
      <div v-else class="space-y-1.5">
        <span class="text-ink-gray-7 text-p-sm-medium">Site name</span>
        <div class="flex items-stretch gap-2">
          <TextInput
            v-model="sitePrefix"
            class="flex-1 min-w-0"
            :class="hasSingleDomain ? '[&_[data-slot=control]]:pe-60' : ''"
            placeholder="mysite"
            @keyup.enter="submit"
          >
            <template v-if="hasSingleDomain" #suffix>
              <span class="text-ink-gray-5 text-p-sm truncate max-w-56">
                {{ wildcardDomains[0] }}
              </span>
            </template>
          </TextInput>

          <!-- Multiple wildcards available -->
          <Select
            v-if="!hasSingleDomain"
            v-model="selectedSuffix"
            class="w-48 shrink-0"
            :options="wildcardDomains.map((d) => ({ label: d, value: d }))"
          />
        </div>

        <p class="mt-1.5 text-ink-gray-5 text-p-sm">
          Web address:
          <span class="font-medium text-ink-gray-7">
            {{ newSiteName || `mysite${selectedSuffix}` }}
          </span>
        </p>
      </div>

      <!-- Choose apps -->
      <div v-if="availableApps.length">
        <div class="flex justify-between items-center mb-2">
          <span class="text-ink-gray-7 text-p-sm-medium">Choose apps</span>
          <span class="text-ink-gray-5 text-xs"> {{ selectedApps.length }} selected </span>
        </div>

        <TextInput v-model="appSearch" class="mb-2" placeholder="Search apps" autocomplete="off">
          <template #prefix>
            <span class="size-4 text-ink-gray-5 lucide-search" />
          </template>
        </TextInput>

        <p v-if="!filteredApps.length" class="py-6 text-ink-gray-5 text-p-sm text-center">
          No apps match "{{ appSearch }}"
        </p>

        <div
          ref="appList"
          :data-fade="fadeEdges"
          class="gap-2 grid md:grid-cols-2 -mx-4 sm:-mx-6 px-4 sm:px-6 max-h-72 overflow-y-auto app-list"
          @scroll.passive="updateFadeEdges"
        >
          <button
            v-for="app in filteredApps"
            :key="app.name"
            type="button"
            class="flex items-center gap-3 px-2 py-2 border rounded-4 min-w-0 text-left transition-colors hover:bg-surface-alpha-gray-1"
            :class="
              selectedApps.includes(app.name)
                ? 'border-outline-gray-2'
                : 'border-transparent '
            "
            @click="toggleApp(app.name)"
          >
            <AppIcon :name="app.name" size="lg" />
            <span class="flex-1 min-w-0 text-ink-gray-8 truncate">
              {{ app.title || app.name }}
            </span>

            <Checkbox
              :model-value="selectedApps.includes(app.name)"
              class="pointer-events-none shrink-0"
            />
          </button>
        </div>
      </div>

      <p class="flex items-start gap-1.5 text-ink-gray-5 text-p-sm">
        <span class="mt-0.5 size-3.5 lucide-info shrink-0" />
        Runs on this server - no extra cost; sites share its compute and storage.
      </p>

      <ErrorMessage v-if="error" class="mt-1" :message="error" />

      <div class="flex justify-end gap-2">
        <Button @click="open = false">Cancel</Button>
        <Button variant="solid" :loading="creating" @click="submit" :disabled="!newSiteName"
          >Create Site</Button
        >
      </div>
    </div>
  </Dialog>
</template>

<style scoped>
/* No transition on mask-image: `none` and a gradient aren't interpolable, and
   animating between them blanks the whole list mid-flight. */
.app-list {
  --fade: 1rem;
}

.app-list[data-fade="top"] {
  mask-image: linear-gradient(to bottom, transparent, #000 var(--fade));
}

.app-list[data-fade="bottom"] {
  mask-image: linear-gradient(to top, transparent, #000 var(--fade));
}

.app-list[data-fade="both"] {
  mask-image: linear-gradient(
    to bottom,
    transparent,
    #000 var(--fade),
    #000 calc(100% - var(--fade)),
    transparent
  );
}
</style>
