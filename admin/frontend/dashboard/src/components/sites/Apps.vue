<script setup lang="ts">
import { Button, Dropdown, type DropdownItem, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SwitchBranchDialog from '@/components/apps/SwitchBranchDialog.vue'
import UninstallAppDialog from '@/components/apps/UninstallAppDialog.vue'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import MarketplaceAppCardSkeleton from '@/components/marketplace/MarketplaceAppCardSkeleton.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import { useSite } from '@/composables/sites/useSite'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { errorMessage } from '@/utils/error'
import { toSentenceCase } from '@/utils/format'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

interface AppCardEntry {
  name: string
  title: string
  label: string
  description: string
  logo_url: string | null
  documentation: string
  website: string
  missing: boolean
  unfinished: boolean
}

const props = defineProps<Props>()
const { session } = useSession()
const router = useRouter()

const { apps, canDisableApps, installedApps, appsLoading, loadApps, reload } = useSite(
  props.siteName,
)
const {
  titleMap,
  descriptionMap,
  logoMap,
  documentationMap,
  websiteMap,
  load: loadRegistry,
} = useAppRegistry()

// The list renders `installedApps`, which rides on the site payload - refreshing the
// app metadata alone would leave a just-disabled app on screen.
const refresh = async () => {
  await Promise.all([reload(), loadApps()])
}

const appDetailMap = computed(() => Object.fromEntries(apps.value.map((a) => [a.name, a])))

// Disabling needs a Frappe that supports it, and an app the catalog can reinstall.
const canDisable = (app: AppCardEntry | null) => {
  return canDisableApps.value && Boolean(app && appDetailMap.value[app.name]?.is_marketplace)
}

// Registry metadata describes the listed repository, not a fork with the same name.
const registryValue = (map: Record<string, string>, name: string) =>
  appDetailMap.value[name]?.is_marketplace ? map[name] : ''

const appObjects = computed<AppCardEntry[]>(() =>
  installedApps.value.map((name) => {
    const detail = appDetailMap.value[name]
    return {
      name,
      title: registryValue(titleMap.value, name) || toSentenceCase(detail?.title) || name,
      label: detail?.missing
        ? 'Not on bench'
        : detail?.install_unfinished
          ? 'Install incomplete'
          : detail?.version || '',
      description: detail?.missing
        ? 'The site lists this app, but the bench does not have it. Uninstall removes it from the site.'
        : detail?.install_unfinished
          ? 'Its install failed after the site listed it. Retry the install to finish it.'
          : registryValue(descriptionMap.value, name) || detail?.description || '',
      logo_url: registryValue(logoMap.value, name) || null,
      documentation: registryValue(documentationMap.value, name),
      website: registryValue(websiteMap.value, name),
      missing: Boolean(detail?.missing),
      unfinished: Boolean(detail?.install_unfinished),
    }
  }),
)

const showUninstall = ref(false)
const uninstallTarget = ref<AppCardEntry | null>(null)

const showSwitchBranch = ref(false)
const switchBranchApp = ref<AppCardEntry | null>(null)
const switchBranchTarget = computed(() => {
  const app = switchBranchApp.value
  const detail = app && appDetailMap.value[app.name]
  return app && detail ? { ...app, branch: detail.branch, repo: detail.repo } : null
})

const openLink = (url: string) => {
  window.open(url, '_blank', 'noopener,noreferrer')
}

const retryInstall = async (app: AppCardEntry) => {
  try {
    const result = await sitesApi.apps.install(props.siteName, { app: app.name })
    if ('task_id' in result && result.task_id) openTaskDetailPage(router, result.task_id)
    else toast.error(apiErrorMessage(result, `Could not retry ${app.title}.`))
  } catch (caught) {
    toast.error(errorMessage(caught, `Could not retry ${app.title}.`))
  }
}

const menuOptions = (app: AppCardEntry): DropdownItem[] => {
  const options: DropdownItem[] = []

  if (app.unfinished)
    options.push({
      label: 'Retry install',
      icon: 'lucide-rotate-ccw',
      onClick: () => retryInstall(app),
    })

  if (session.codeEditor)
    options.push({
      label: 'Open in editor',
      icon: 'lucide-code',
      onClick: () => openLink(`/editor/${encodeURIComponent(app.name)}`),
    })

  if (app.website)
    options.push({ label: 'Website', icon: 'lucide-globe', onClick: () => openLink(app.website) })

  if (app.documentation)
    options.push({
      label: 'Documentation',
      icon: 'lucide-book-open',
      onClick: () => openLink(app.documentation),
    })

  // Branches are listed through the GitHub provider.
  if (appDetailMap.value[app.name]?.repo?.includes('github.com'))
    options.push({
      label: 'Switch branch',
      icon: 'lucide-git-branch',
      onClick: () => {
        switchBranchApp.value = app
        showSwitchBranch.value = true
      },
    })

  if (app.name !== 'frappe')
    options.push({
      label: canDisable(app) ? 'Remove' : 'Uninstall',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => {
        uninstallTarget.value = app
        showUninstall.value = true
      },
    })

  return options
}

onMounted(() => {
  loadApps()
  loadRegistry()
})
</script>

<template>
  <div class="grid md:grid-cols-2 gap-2 mt-1">
    <template v-if="appsLoading">
      <MarketplaceAppCardSkeleton v-for="i in 4" :key="i" :index="i - 1" />
    </template>

    <p
      v-else-if="!installedApps.length"
      class="col-span-full py-12 text-ink-gray-5 text-sm text-center"
    >
      No apps installed on this site.
    </p>

    <template v-else>
      <MarketplaceAppCard v-for="app in appObjects" :key="app.name" :app="app">
        <template #actions>
          <Dropdown v-if="menuOptions(app).length" :options="menuOptions(app)">
            <template #default="{ open }">
              <Button
                variant="ghost"
                :active="open"
                icon="lucide-ellipsis"
                label="App actions"
                tooltip="Actions"
              />
            </template>
          </Dropdown>

          <span v-else class="size-7 shrink-0" />
        </template>
      </MarketplaceAppCard>
    </template>
  </div>

  <SwitchBranchDialog v-model:open="showSwitchBranch" :app="switchBranchTarget" />

  <UninstallAppDialog
    v-model:open="showUninstall"
    :app="uninstallTarget"
    :site-name="siteName"
    :can-disable="canDisable(uninstallTarget)"
    @disabled="refresh"
  />
</template>
