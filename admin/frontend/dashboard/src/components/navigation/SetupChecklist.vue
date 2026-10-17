<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  Select,
  SidebarCard,
  Spinner,
  TextInput,
  toast,
} from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'
import CircularProgress from '@/components/common/CircularProgress.vue'

import { settingsApi } from '@/api/settings'
import { useGithubConnection } from '@/composables/settings/useGithubConnection'
import { useObjectStorage } from '@/composables/settings/useObjectStorage'
import { useNewSite } from '@/composables/sites/useNewSite'
import { useSites } from '@/composables/sites/useSites'
import { errorMessage } from '@/utils/error'

const route = useRoute()
const { sites, load: loadSites } = useSites()
const storage = reactive(useObjectStorage())
const github = reactive(useGithubConnection())
const site = reactive(useNewSite())

const onboarding = ref('')
const loaded = ref(false)

const steps = computed(() => [
  {
    id: 'site',
    name: 'First site',
    label: 'Create your first site',
    icon: 'lucide-rocket',
    title: 'Launch your first site',
    description: 'Pick a name and apps. Pilot handles the rest.',
    action: 'Create site',
    savingLabel: 'Creating site',
    done: sites.value.length > 0,
    saving: site.creating,
    canSave: Boolean(site.name),
    save: createSite,
  },
  {
    id: 'github',
    name: 'GitHub',
    label: 'Connect GitHub',
    icon: 'lucide-git-branch',
    title: 'Connect your GitHub account',
    description: 'Lets Pilot install apps from private repos.',
    action: 'Next',
    savingLabel: 'Verifying token',
    done: github.connected,
    saving: github.connecting,
    canSave: Boolean(github.token.trim()),
    save: github.connect,
  },
  {
    id: 'storage',
    name: 'Storage',
    label: 'Connect object storage',
    icon: 'lucide-database',
    title: 'Where should backups go?',
    description: 'Pick an S3-compatible bucket for offsite backups.',
    action: 'Next',
    savingLabel: 'Connecting storage',
    done: storage.connected,
    saving: storage.saving,
    canSave: storage.canSave,
    save: storage.save,
  },
])

const doneCount = computed(() => steps.value.filter((step) => step.done).length)

const isVisible = computed(
  () => loaded.value && onboarding.value !== 'dismissed' && doneCount.value < steps.value.length,
)

const progress = computed(() => (doneCount.value / steps.value.length) * 100)

const isOpen = ref(false)
const current = ref(0)
const currentStep = computed(() => steps.value[current.value])

const nodeState = (index: number) => {
  if (steps.value[index].done) return 'done'
  if (index === current.value) return 'current'

  return index < current.value ? 'skipped' : 'upcoming'
}

const railClasses = {
  done: 'border-outline-green-5 text-ink-green-7',
  current: 'bg-surface-base border-dashed border-outline-gray-9 text-ink-gray-9',
  skipped: 'border-dashed border-outline-gray-3 text-ink-gray-5',
  upcoming: 'border-dashed border-outline-gray-3 text-ink-gray-5',
}

const statusLabels = {
  done: 'Done',
  current: 'In progress',
  skipped: 'Skipped',
  upcoming: 'Up next',
}

const dialogOpen = computed({
  get: () => isOpen.value,
  set: (open) => {
    if (!open) minimize()
  },
})

const firstPending = () => {
  const index = steps.value.findIndex((step) => !step.done)

  return Math.max(0, index)
}

const open = (index = firstPending()) => {
  current.value = index
  isOpen.value = true
}

const advance = () => {
  const index = steps.value.findIndex((step, i) => i > current.value && !step.done)

  if (index === -1) minimize()
  else current.value = index
}

const submit = async () => {
  const step = currentStep.value

  if (step.done || (await step.save())) advance()
}

const createSite = async () => {
  const name = site.name
  const taskId = await site.create()

  if (!taskId) return false

  toast.success(`Creating ${name}. You can keep setting up meanwhile.`)
  await loadSites()

  return true
}

const saveOnboarding = async (state: string) => {
  onboarding.value = state

  try {
    await settingsApi.update({ bench: { onboarding: state } })
  } catch (caught) {
    toast.error(errorMessage(caught, 'Could not save onboarding progress.'))
  }
}

const minimize = () => {
  isOpen.value = false

  if (onboarding.value !== 'minimized') saveOnboarding('minimized')
}

const refresh = async () => {
  if (onboarding.value === 'dismissed') return

  try {
    const [settings] = await Promise.all([
      settingsApi.get(),
      storage.load(),
      github.load(),
      loadSites(),
    ])

    onboarding.value = settings.bench.onboarding
    loaded.value = true

    if (!onboarding.value && isVisible.value && !isOpen.value) open()
  } catch {
    loaded.value = false
  }
}

watch(
  () => route.name,
  (_name, previous) => {
    if (previous === 'Settings') refresh()
  },
)

watch(
  () => isOpen.value && currentStep.value.id === 'site',
  (showing) => {
    if (showing) site.reset()
  },
)

onMounted(refresh)
</script>

<template>
  <Dialog v-model:open="dialogOpen" bare size="3xl">
    <div class="flex sm:flex-row flex-col">
      <aside
        class="bg-surface-gray-1 px-7 pt-7 pb-6 border-b sm:border-r sm:border-b-0 sm:w-56 shrink-0"
      >
        <h2 class="text-ink-gray-8 text-base-semibold">Get started</h2>
        <p class="flex items-center gap-1.5 mt-1 text-ink-gray-5 text-sm">
          <CircularProgress :value="progress" />
          {{ doneCount }} of {{ steps.length }} done
        </p>

        <ol aria-hidden="true" class="mt-6">
          <li
            v-for="(step, index) in steps"
            :key="step.id"
            class="relative gap-x-3 gap-y-0.5 grid grid-flow-col grid-rows-2 justify-start pb-5 last:pb-0 after:absolute after:top-9 after:bottom-1 after:left-4 last:after:hidden"
            :class="
              step.done
                ? 'after:w-px after:bg-surface-green-7'
                : 'after:border-l after:border-outline-gray-3 after:border-dashed'
            "
          >
            <span
              class="place-items-center grid row-span-2 border rounded-4 size-8"
              :class="railClasses[nodeState(index)]"
            >
              <span class="size-4" :class="step.icon" />
            </span>

            <span
              class="text-sm"
              :class="index === current ? 'text-ink-gray-9 text-sm-medium' : 'text-ink-gray-7'"
            >
              {{ step.name }}
            </span>

            <span class="text-ink-gray-5 text-xs">{{ statusLabels[nodeState(index)] }}</span>
          </li>
        </ol>
      </aside>

      <div class="flex flex-col flex-1 min-w-0">
        <section class="flex flex-col sm:min-h-80 px-7 pt-7 pb-2">
          <h2 class="text-ink-gray-8 text-2xl-semibold">{{ currentStep.title }}</h2>
          <p class="mt-1.5 mb-5 text-ink-gray-6 text-p-base">{{ currentStep.description }}</p>

          <template v-if="currentStep.id === 'storage'">
            <div class="gap-3 grid sm:grid-cols-2">
              <TextInput v-model="storage.bucket" label="Bucket" placeholder="pilot-backups" />
              <Select
                v-model="storage.region"
                label="Region"
                :options="storage.regionOptions"
                :disabled="storage.loadingRegions"
              />

              <TextInput
                v-model="storage.endpointUrl"
                class="sm:col-span-2"
                label="Endpoint URL"
                placeholder="Optional for AWS, DigitalOcean, and Hetzner"
              />

              <TextInput v-model="storage.accessKey" label="Access key" placeholder="AKIA…" />
              <TextInput
                v-model="storage.secretKey"
                label="Secret key"
                type="password"
                :placeholder="storage.secretKeySet ? 'Saved, leave blank to keep' : undefined"
              />
            </div>

            <ErrorMessage class="mt-4" :message="storage.error" />
          </template>

          <template v-else-if="currentStep.id === 'github'">
            <TextInput v-model="github.username" label="GitHub username" placeholder="octocat" />

            <TextInput
              v-model="github.token"
              class="mt-3.5"
              label="Personal access token"
              type="password"
              placeholder="ghp_…"
              @keydown.enter="submit"
            >
              <template #description>
                Needs the <code>repo</code> scope.
                <a :href="github.tokenHelpUrl" target="_blank" rel="noopener" class="underline">
                  Generate one
                </a>
              </template>
            </TextInput>

            <ErrorMessage class="mt-4" :message="github.error" />
          </template>

          <template v-else>
            <Spinner v-if="site.loading" class="mx-auto my-16 size-5 text-ink-gray-4" />

            <template v-else>
              <TextInput
                v-if="!site.wildcardDomains.length"
                v-model="site.name"
                label="Site name"
                placeholder="mysite.localhost"
                @keydown.enter="submit"
              />

              <div v-else class="flex items-end gap-2">
                <TextInput
                  v-model="site.prefix"
                  class="flex-1"
                  label="Site name"
                  placeholder="mysite"
                  @keydown.enter="submit"
                >
                  <template v-if="site.hasSingleDomain" #suffix>
                    <span class="text-ink-gray-5 text-sm">{{ site.wildcardDomains[0] }}</span>
                  </template>
                </TextInput>

                <Select
                  v-if="!site.hasSingleDomain"
                  v-model="site.suffix"
                  class="w-48 shrink-0"
                  :options="site.wildcardDomains.map((domain) => ({ label: domain, value: domain }))"
                />
              </div>

              <template v-if="site.availableApps.length">
                <p class="flex justify-between mt-5 mb-1.5 text-ink-gray-6 text-sm">
                  Apps
                  <span class="text-ink-gray-5">{{ site.selectedApps.length }} selected</span>
                </p>

                <TextInput
                  v-model="site.appSearch"
                  class="mb-2"
                  placeholder="Search apps"
                  autocomplete="off"
                >
                  <template #prefix>
                    <span class="size-4 text-ink-gray-5 lucide-search" />
                  </template>
                </TextInput>

                <p
                  v-if="!site.filteredApps.length"
                  class="py-6 text-ink-gray-5 text-p-sm text-center"
                >
                  No apps match "{{ site.appSearch }}"
                </p>

                <div
                  class="app-list content-start gap-2 grid sm:grid-cols-2 -mr-4 pr-4 pb-6 max-h-72 overflow-y-auto [scrollbar-color:var(--outline-gray-3)_transparent] [scrollbar-width:thin]"
                >
                  <button
                    v-for="app in site.filteredApps"
                    :key="app.name"
                    type="button"
                    :aria-pressed="site.selectedApps.includes(app.name)"
                    class="flex items-center gap-3 p-2 border border-outline-gray-2 aria-pressed:border-outline-gray-5 rounded-4 text-left hover:bg-surface-gray-2"
                    @click="site.toggleApp(app.name)"
                  >
                    <AppIcon :name="app.name" size="md" />

                    <span class="flex-1 min-w-0 text-ink-gray-8 text-base truncate">
                      {{ app.title || app.name }}
                    </span>

                    <Checkbox
                      :model-value="site.selectedApps.includes(app.name)"
                      class="pointer-events-none shrink-0"
                    />
                  </button>
                </div>
              </template>

              <ErrorMessage class="mt-4" :message="site.error" />
            </template>
          </template>
        </section>

        <footer class="flex items-center gap-2 px-7 pt-4.5 pb-5">
          <Button
            v-if="current > 0"
            class="mr-auto -ml-2"
            variant="ghost"
            icon-left="lucide-arrow-left"
            label="Back"
            @click="current--"
          />
          <Button
            v-else
            class="mr-auto -ml-2"
            variant="ghost"
            label="Finish later"
            @click="minimize"
          />

          <Button
            v-if="!currentStep.done && current < steps.length - 1"
            variant="ghost"
            label="Skip"
            @click="advance"
          />

          <Button
            variant="solid"
            :label="currentStep.done ? 'Next' : currentStep.action"
            :loading="currentStep.saving"
            :loading-text="currentStep.savingLabel"
            :disabled="!currentStep.done && !currentStep.canSave"
            @click="submit"
          />
        </footer>
      </div>
    </div>
  </Dialog>

  <SidebarCard
    v-if="isVisible && onboarding === 'minimized'"
    dismissible
    class="group-data-[state=collapsed]:hidden dark:!bg-surface-gray-1 mx-2 mb-2 !border-0 shadow-sm"
    @dismiss="saveOnboarding('dismissed')"
  >
    <template #title>
      <button type="button" @click="open()">Get started</button>
    </template>

    <template #prefix>
      <CircularProgress :value="progress" />
    </template>

    <template #description>{{ doneCount }} of {{ steps.length }} done</template>

    <template #actions>
      <template v-for="(step, index) in steps" :key="step.id">
        <p
          v-if="step.done"
          class="flex items-center gap-2 py-1.5 text-ink-gray-5 text-sm before:size-4 before:text-ink-green-6 before:lucide-circle-check"
        >
          {{ step.label }}
        </p>

        <button
          v-else
          type="button"
          class="flex items-center gap-2 -mx-1.5 px-1.5 py-1.5 rounded-4 hover:bg-surface-gray-2 text-ink-gray-8 text-sm before:size-4 before:text-ink-gray-4 before:lucide-circle-dashed after:ml-auto after:size-3.5 after:text-ink-gray-4 hover:after:text-ink-gray-6 after:lucide-chevron-right"
          @click="open(index)"
        >
          {{ step.label }}
        </button>
      </template>
    </template>
  </SidebarCard>
</template>

<style scoped>
.app-list {
  mask-image: linear-gradient(to bottom, #000 calc(100% - 1.5rem), transparent);
}
</style>
