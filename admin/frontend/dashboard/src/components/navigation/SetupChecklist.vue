<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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
import SetupIllustration from '@/components/navigation/SetupIllustration.vue'

import { settingsApi } from '@/api/settings'
import { useGithubConnection } from '@/composables/settings/useGithubConnection'
import { useObjectStorage } from '@/composables/settings/useObjectStorage'
import { useNewSite } from '@/composables/sites/useNewSite'
import { useSites } from '@/composables/sites/useSites'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const { sites, load: loadSites } = useSites()
const storage = reactive(useObjectStorage())
const github = reactive(useGithubConnection())
const site = reactive(useNewSite())

const onboarding = ref('')
const siteStarted = ref(false)
const useToken = ref(false)
const origin = window.location.origin

const githubViaCloud = computed(() => github.centralAvailable && !useToken.value)
const githubRepositories = computed(() =>
  github.centralAccounts.flatMap((account) => account.repositories),
)

const loaded = ref(false)

const githubSteps = [
  {
    icon: 'lucide-key-round',
    title: 'Authorize on GitHub',
    description: 'A GitHub window asks which repos to share.',
  },
  {
    icon: 'lucide-folder-git-2',
    title: 'Pick your app repos',
    description: 'Only the repos that hold your Frappe apps.',
  },
  {
    icon: 'lucide-undo-2',
    title: 'Land back here',
    description: 'Straight on to storage and your first site.',
  },
]

const steps = computed(() => [
  {
    id: 'github',
    name: 'GitHub',
    label: 'Connect GitHub',
    title: github.viaCentral ? 'GitHub connected' : 'Connect your GitHub',
    description: github.viaCentral
      ? 'Pilot can read these repos through Frappe Cloud.'
      : 'Pilot installs your Frappe apps straight from private repos. Takes about a minute.',
    action: githubViaCloud.value ? 'Connect GitHub' : 'Connect',
    savingLabel: githubViaCloud.value ? 'Opening Frappe Cloud' : 'Verifying token',
    done: github.connected,
    saving: github.connecting || github.redirecting,
    canSave: githubViaCloud.value || Boolean(github.token.trim()),
    save: githubViaCloud.value
      ? () => github.connectViaCentral(`${origin}${route.path}?github=connected`)
      : github.connect,
  },
  {
    id: 'storage',
    name: 'Storage',
    label: 'Connect object storage',
    title: 'Where should backups go?',
    description: 'Pick an S3-compatible bucket for offsite backups.',
    action: 'Continue',
    savingLabel: 'Connecting storage',
    done: storage.connected,
    saving: storage.saving,
    canSave: storage.canSave,
    save: storage.save,
  },
  {
    id: 'site',
    name: 'First site',
    label: 'Create your first site',
    title: 'Launch your first site',
    description: 'Pick a name and apps. Pilot handles the rest.',
    action: 'Create site',
    savingLabel: undefined,
    done: siteStarted.value || sites.value.length > 0,
    saving: site.creating,
    canSave: !site.nameError,
    save: createSite,
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

  return 'upcoming'
}

const nodeStyles = {
  done: {
    classes: 'bg-surface-green-2 border-outline-green-3 text-ink-green-6',
    icon: 'lucide-check',
  },
  current: {
    classes: 'border-outline-gray-7 text-ink-gray-8',
    icon: '',
  },
  upcoming: {
    classes: 'border-dashed border-outline-gray-4 text-ink-gray-5',
    icon: '',
  },
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

  loadSites()
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
  const taskId = await site.create()

  if (!taskId) return false

  siteStarted.value = true
  loadSites()
  openTaskDetailPage(router, taskId)

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

    const returnedFromGithub = route.query.github === 'connected'

    if (returnedFromGithub) {
      router.replace({ query: { ...route.query, github: undefined } })

      if (github.viaCentral) toast.success('GitHub connected through Frappe Cloud')
    }

    if ((returnedFromGithub || !onboarding.value) && isVisible.value && !isOpen.value) open()
  } catch (caught) {
    loaded.value = false
    toast.error(errorMessage(caught, 'Could not load onboarding progress.'))
  }
}

watch(
  () => route.name,
  (_name, previous) => {
    if (previous === 'Settings') refresh()
  },
)

watch(isOpen, (opened) => {
  if (opened) site.reset()
})

onMounted(refresh)
</script>

<template>
  <Dialog v-model:open="dialogOpen" bare size="4xl">
    <div class="flex">
      <aside
        class="max-sm:hidden flex flex-col bg-[color-mix(in_oklab,var(--surface-base)_97%,var(--ink-gray-9))] p-8 border-r w-96 shrink-0"
      >
        <ol aria-hidden="true" class="flex items-center gap-2">
          <li
            v-for="(step, index) in steps"
            :key="step.id"
            class="flex items-center gap-2 before:w-4 before:border-t before:transition-colors before:duration-300 first:before:hidden"
            :class="
              steps[index - 1]?.done
                ? 'before:border-outline-green-5'
                : 'before:border-dashed before:border-outline-gray-4'
            "
          >
            <span
              class="place-items-center grid border rounded-3 size-6 text-xs tabular-nums leading-none transition-colors duration-300 shrink-0"
              :class="nodeStyles[nodeState(index)].classes"
            >
              <span
                v-if="nodeStyles[nodeState(index)].icon"
                class="size-3.5"
                :class="nodeStyles[nodeState(index)].icon"
              />
              <template v-else>{{ index + 1 }}</template>
            </span>

            <span
              class="whitespace-nowrap"
              :class="index === current ? 'text-ink-gray-8 text-sm-medium' : 'text-ink-gray-6 text-sm'"
            >
              {{ step.name }}
            </span>
          </li>
        </ol>

        <SetupIllustration
          :key="currentStep.id"
          class="my-auto pt-4 fade-in"
          :step="currentStep.id"
        />
      </aside>

      <div class="flex flex-col flex-1 min-w-0">
        <section class="flex flex-col flex-1 px-7 sm:px-8 pt-8 pb-2">
          <h2 class="text-ink-gray-8 text-2xl-semibold">{{ currentStep.title }}</h2>
          <p class="mt-2 mb-6 text-ink-gray-6 text-p-base">{{ currentStep.description }}</p>

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

              <TextInput
                v-model="storage.accessKey"
                label="Access key"
                placeholder="AKIA…"
                autocomplete="off"
              />

              <TextInput
                v-model="storage.secretKey"
                label="Secret key"
                type="password"
                autocomplete="new-password"
                :placeholder="storage.secretKeySet ? 'Saved, leave blank to keep' : '••••••••'"
              />
            </div>

            <ErrorMessage class="mt-4" :message="storage.error" />
          </template>

          <template v-else-if="currentStep.id === 'github' && github.viaCentral">
            <ul class="-mt-4 max-h-64 overflow-y-auto">
              <li
                v-for="repository in githubRepositories"
                :key="repository"
                class="flex items-center gap-2.5 h-8 text-ink-gray-8 text-base before:size-4 before:text-ink-gray-5 before:lucide-package"
              >
                <span class="truncate">
                  <span class="text-ink-gray-5">{{ repository.split('/')[0] }}/</span>
                  {{ repository.split('/')[1] }}
                </span>
              </li>
            </ul>

            <Button
              class="self-start mt-2"
              icon-left="lucide-settings-2"
              label="Manage repositories"
              :loading="github.redirecting"
              @click="github.connectViaCentral(`${origin}${route.path}?github=connected`)"
            />
          </template>

          <template v-else-if="currentStep.id === 'github'">
            <ol v-if="githubViaCloud">
              <li
                v-for="item in githubSteps"
                :key="item.title"
                class="relative gap-x-3.5 gap-y-1 grid grid-flow-col grid-rows-[auto_auto] justify-start pb-5 last:pb-0 after:absolute after:top-9 after:-bottom-px after:left-3.5 after:w-px after:bg-surface-gray-3 last:after:hidden"
              >
                <span
                  class="place-items-center grid row-span-2 self-center bg-surface-gray-1 border border-outline-gray-2 rounded-full size-7 text-ink-gray-6"
                >
                  <span class="size-3.5" :class="item.icon" />
                </span>

                <span class="text-ink-gray-8 text-base-medium">{{ item.title }}</span>
                <span class="text-ink-gray-5 text-sm">{{ item.description }}</span>
              </li>
            </ol>

            <template v-else>
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
            </template>

            <ErrorMessage class="mt-4" :message="github.error" />

            <Button
              v-if="github.centralAvailable && !github.connected"
              class="self-start mt-6"
              :label="useToken ? 'Use Frappe Cloud instead' : 'Use a token instead'"
              @click="useToken = !useToken"
            />
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
                  class="place-items-center grid h-56 text-ink-gray-5 text-p-sm"
                >
                  No apps match "{{ site.appSearch }}"
                </p>

                <div
                  v-else
                  class="app-list content-start gap-2 grid sm:grid-cols-2 pb-6 h-56 overflow-y-auto [scrollbar-width:none]"
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

        <footer class="flex items-center gap-2 px-7 sm:px-8 pt-4.5 pb-8">
          <Button
            v-if="current > 0"
            class="mr-auto"
            variant="ghost"
            icon-left="lucide-arrow-left"
            label="Back"
            @click="current--"
          />
          <Button v-else class="mr-auto" variant="ghost" label="Finish later" @click="minimize" />

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
          class="flex items-center gap-2 py-1.5 text-ink-gray-5 text-sm before:size-4 before:text-ink-green-7 before:lucide-circle-check"
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
