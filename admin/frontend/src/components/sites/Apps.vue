<template>
  <div class="mt-5">
    <div v-if="appsLoading" class="flex justify-center py-12">
      <LoadingText />
    </div>
    <div v-else-if="!installedApps.length" class="py-12 text-ink-gray-5 text-sm text-center">
      No apps installed on this site.
    </div>
    <div v-else class="gap-x-6 gap-y-4 grid grid-cols-1 md:grid-cols-2">
      <MarketplaceAppCard v-for="app in appObjects" :key="app.name" :app="app">
        <template #actions>
          <Dropdown v-if="menuOptions(app).length" :options="menuOptions(app)" placement="bottom-end">
            <template #default="{ open }">
              <Button variant="ghost" size="sm" :active="open">
                <span class="size-4 lucide-ellipsis" />
              </Button>
            </template>
          </Dropdown>
          <span v-else class="size-7 shrink-0" />
        </template>
      </MarketplaceAppCard>
    </div>
  </div>

  <Dialog v-model="showUninstall" :options="{ title: 'Uninstall App', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Uninstall <strong>{{ uninstallTarget }}</strong> from {{ siteName }}?
      </p>
      <ErrorMessage v-if="uninstallError" :message="uninstallError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showUninstall = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="uninstalling" @click="confirmUninstall">
          Uninstall
        </Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, Dropdown, ErrorMessage, LoadingText } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import MarketplaceAppCard from '@/components/marketplace/MarketplaceAppCard.vue'
import { useSite } from '@/composables/sites/useSite'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { openTaskDetailPage } from '@/utils/taskRoute'
import { toSentenceCase } from '@/utils/format'

const props = defineProps({
  siteName: { type: String, required: true },
})
const router = useRouter()

const { apps, installedApps, appsLoading, loadApps, uninstallApp } = useSite(props.siteName)
const { titleMap, descriptionMap, logoMap, documentationMap, websiteMap, load: loadRegistry } = useAppRegistry()

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
const uninstallTarget = ref('')
const uninstalling = ref(false)
const uninstallError = ref('')

function openLink(url) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

function menuOptions(app) {
  return [
    { label: 'Open in editor', icon: 'lucide-code', onClick: () => openLink(`/editor/${encodeURIComponent(app.name)}`) },
    ...(app.website
      ? [{ label: 'Website', icon: 'lucide-globe', onClick: () => openLink(app.website) }]
      : []),
    ...(app.documentation
      ? [{ label: 'Documentation', icon: 'lucide-book-open', onClick: () => openLink(app.documentation) }]
      : []),
    ...(app.name !== 'frappe' ? [{
      label: 'Uninstall',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => { uninstallTarget.value = app.name; uninstallError.value = ''; showUninstall.value = true },
    }] : []),
  ]
}

async function confirmUninstall() {
  uninstalling.value = true
  uninstallError.value = ''
  try {
    const result = await uninstallApp(uninstallTarget.value)
    if (result.task_id) {
      showUninstall.value = false
      openTaskDetailPage(router, result.task_id)
    } else {
      uninstallError.value = apiErrorMessage(result, 'Uninstall failed.')
    }
  } catch (e) {
    uninstallError.value = e.message || 'Uninstall failed.'
  } finally {
    uninstalling.value = false
  }
}

onMounted(() => {
  loadApps()
  loadRegistry()
})
</script>
