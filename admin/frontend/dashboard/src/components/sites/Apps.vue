<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button, Dropdown } from 'frappe-ui'

import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import MarketplaceAppCardSkeleton from '@/components/marketplace/MarketplaceAppCardSkeleton.vue'
import UninstallAppDialog from '@/components/apps/UninstallAppDialog.vue'

import { useSite } from '@/composables/sites/useSite'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import { toSentenceCase } from '@/utils/format'

interface Props {
  siteName: string
}

const props = defineProps<Props>()
const { session } = useSession()

const { apps, canDisableApps, installedApps, appsLoading, loadApps, reload } = useSite(
  props.siteName,
)
const {
  registry,
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

// Disabling needs a Frappe that supports it, and an app the catalog can reinstall.
const canDisable = (app) => {
  return (
    canDisableApps.value && Boolean(app && registry.value.some((entry) => entry.name === app.name))
  )
}

const appDetailMap = computed(() => Object.fromEntries(apps.value.map((a) => [a.name, a])))

const appObjects = computed(() =>
  installedApps.value.map((name) => ({
    name,
    title: titleMap.value[name] || toSentenceCase(appDetailMap.value[name]?.title) || name,
    label: appDetailMap.value[name]?.version || '',
    description: descriptionMap.value[name] || appDetailMap.value[name]?.description || '',
    logo_url: logoMap.value[name] || null,
    documentation: documentationMap.value[name] || '',
    website: websiteMap.value[name] || '',
  })),
)

const showUninstall = ref(false)
const uninstallTarget = ref(null)

const openLink = (url) => {
  window.open(url, '_blank', 'noopener,noreferrer')
}

const menuOptions = (app) => {
  return [
    ...(session.developerMode
      ? [
          {
            label: 'Open in editor',
            icon: 'lucide-code',
            onClick: () => openLink(`/editor/${encodeURIComponent(app.name)}`),
          },
        ]
      : []),
    ...(app.website
      ? [{ label: 'Website', icon: 'lucide-globe', onClick: () => openLink(app.website) }]
      : []),
    ...(app.documentation
      ? [
          {
            label: 'Documentation',
            icon: 'lucide-book-open',
            onClick: () => openLink(app.documentation),
          },
        ]
      : []),
    ...(app.name !== 'frappe'
      ? [
          {
            label: canDisable(app) ? 'Remove' : 'Uninstall',
            icon: 'lucide-trash-2',
            theme: 'red',
            onClick: () => {
              uninstallTarget.value = app
              showUninstall.value = true
            },
          },
        ]
      : []),
  ]
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

    <p v-else-if="!installedApps.length" class="col-span-full py-12 text-ink-gray-5 text-sm text-center">
      No apps installed on this site.
    </p>

    <template v-else>
      <MarketplaceAppCard v-for="app in appObjects" :key="app.name" :app="app">
        <template #actions>
          <Dropdown
            v-if="menuOptions(app).length"
            :options="menuOptions(app)"
          >
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

  <UninstallAppDialog
    v-model:open="showUninstall"
    :app="uninstallTarget"
    :site-name="siteName"
    :can-disable="canDisable(uninstallTarget)"
    @disabled="refresh"
  />
</template>
