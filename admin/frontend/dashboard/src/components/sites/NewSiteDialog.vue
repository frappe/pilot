<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, Select, Spinner, TextInput } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'

import { apiErrorMessage } from '@/api/client'
import { appsApi } from '@/api/apps'
import { sitesApi } from '@/api/sites'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { buildSiteAppChoices } from '@/utils/siteApps'

interface Props {
  sites?: any[]
}

withDefaults(defineProps<Props>(), {
  sites: () => [],
})

const emit = defineEmits(['started'])
const open = defineModel()

const { registry, load: loadRegistry } = useAppRegistry()
const benchApps = ref([])

const newSiteName = ref('')
const sitePrefix = ref('')
const wildcardDomains = ref([])
const selectedSuffix = ref('')
const loading = ref(false)
const creating = ref(false)
const error = ref('')

const selectedApps = ref([])

const hasSingleDomain = computed(() => wildcardDomains.value.length === 1)

// Fade only the edges that can still scroll, so a clipped row reads as "more
// below" and the last app is never dimmed at rest.
const appList = ref(null)
const fadeEdges = ref('')

const updateFadeEdges = () => {
  const el = appList.value
  if (!el) return
  const top = el.scrollTop > 1
  const bottom = Math.ceil(el.scrollTop + el.clientHeight) < el.scrollHeight - 1
  fadeEdges.value = top && bottom ? 'both' : top ? 'top' : bottom ? 'bottom' : ''
}

const availableApps = computed(() => buildSiteAppChoices(registry.value, benchApps.value))

// Also `loading`: apps resolve while the spinner is still up, so the rows only
// exist once it flips.
watch([availableApps, loading], () => nextTick(updateFadeEdges))

watch([sitePrefix, selectedSuffix], () => {
  if (wildcardDomains.value.length && sitePrefix.value) {
    newSiteName.value = `${sitePrefix.value.trim()}${selectedSuffix.value}`
  } else {
    newSiteName.value = ''
  }
})

watch(open, (visible) => {
  if (!visible) return
  reset()
})

const reset = async () => {
  newSiteName.value = ''
  sitePrefix.value = ''
  error.value = ''
  selectedApps.value = []
  loading.value = true
  await Promise.all([loadWildcardDomains(), loadRegistry(), loadBenchApps()])
  loading.value = false
}

const loadBenchApps = async () => {
  try {
    benchApps.value = await appsApi.installed()
  } catch {
    benchApps.value = []
  }
}

const toggleApp = (name) => {
  const index = selectedApps.value.indexOf(name)
  if (index === -1) selectedApps.value.push(name)
  else selectedApps.value.splice(index, 1)
}

const loadWildcardDomains = async () => {
  try {
    const { domains } = await sitesApi.domains.wildcardList()
    wildcardDomains.value = domains || []
    selectedSuffix.value = wildcardDomains.value[0] || ''
  } catch {
    wildcardDomains.value = []
  }
}

const validate = (name) => {
  if (!name) return 'Site name is required.'
  if (!/^[a-zA-Z0-9][a-zA-Z0-9\-.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$/.test(name))
    return 'Site name must be a valid hostname.'
  return null
}

const submit = async () => {
  const name = newSiteName.value.trim()
  const validationError = validate(name)
  if (validationError) {
    error.value = validationError
    return
  }

  creating.value = true
  error.value = ''
  try {
    const result = await sitesApi.create({
      name,
      apps: selectedApps.value,
    })
    if (result.task_id) {
      open.value = false
      emit('started', result.task_id)
    } else {
      error.value = apiErrorMessage(result, 'Could not create site.')
    }
  } catch (caught) {
    error.value = caught.message || 'Could not create site.'
  } finally {
    creating.value = false
  }
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
            :class="hasSingleDomain ? '[&_[data-slot=control]]:pe-28' : ''"
            placeholder="mysite"
            @keyup.enter="submit"
          >
            <template v-if="hasSingleDomain" #suffix>
              <span class="text-ink-gray-5 text-p-sm truncate max-w-24">
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
          <span class="font-medium text-ink-gray-7"
            >{{ newSiteName || `mysite${selectedSuffix}` }}</span
          >
        </p>
      </div>

      <!-- Choose apps -->
      <div v-if="availableApps.length">
        <div class="flex justify-between items-center mb-2">
          <span class="text-ink-gray-7 text-p-sm-medium">Choose apps</span>
          <span class="text-ink-gray-5 text-xs"> {{ selectedApps.length }} selected </span>
        </div>

        <div
          ref="appList"
          :data-fade="fadeEdges"
          class="gap-2 grid md:grid-cols-2 -mx-4 sm:-mx-6 px-4 sm:px-6 max-h-72 overflow-y-auto app-list"
          @scroll.passive="updateFadeEdges"
        >
          <button
            v-for="app in availableApps"
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
