<template>
  <div class="mt-5">
    <div v-if="appsLoading" class="flex justify-center py-12">
      <LoadingText />
    </div>
    <div v-else-if="!installedApps.length" class="py-12 text-ink-gray-5 text-sm text-center">
      No apps installed on this site.
    </div>
    <div v-else class="gap-x-6 grid grid-cols-1 sm:grid-cols-2">
      <MarketplaceAppCard v-for="app in appObjects" :key="app.name" :app="app" :show-divider="true">
        <template #actions>
          <Dropdown :options="menuOptions(app.name)" placement="bottom-end">
            <template #default="{ open }">
              <Button variant="ghost" size="sm" :active="open">
                <span class="size-4 lucide-ellipsis" />
              </Button>
            </template>
          </Dropdown>
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
import { Button, Dialog, Dropdown, ErrorMessage, LoadingText } from 'frappe-ui'
import MarketplaceAppCard from '@/components/MarketplaceAppCard.vue'
import { useSite } from '@/composables/useSite'
import { useAppRegistry } from '@/composables/useAppRegistry'

const props = defineProps({
  siteName: { type: String, required: true },
})

const { apps, installedApps, appsLoading, load, loadApps, uninstallApp } = useSite(props.siteName)
const { titleMap, descriptionMap, logoMap, load: loadRegistry } = useAppRegistry()

const appDetailMap = computed(() => Object.fromEntries(apps.value.map((a) => [a.name, a])))

const appObjects = computed(() =>
  installedApps.value.map((name) => ({
    name,
    title: titleMap.value[name] || name,
    label: appDetailMap.value[name]?.version || '',
    description: descriptionMap.value[name] || '',
    logo_url: logoMap.value[name] || null,
  })),
)

const showUninstall = ref(false)
const uninstallTarget = ref('')
const uninstalling = ref(false)
const uninstallError = ref('')

function menuOptions(appName) {
  return [
    { label: 'Documentation', icon: 'lucide-book-open', onClick: () => { } },
    ...(appName !== 'frappe' ? [{
      label: 'Uninstall',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => { uninstallTarget.value = appName; uninstallError.value = ''; showUninstall.value = true },
    }] : []),
  ]
}

async function confirmUninstall() {
  uninstalling.value = true
  uninstallError.value = ''
  try {
    const result = await uninstallApp(uninstallTarget.value)
    if (result?.ok) {
      showUninstall.value = false
      await load()
      await loadApps()
    } else {
      uninstallError.value = result?.error || 'Uninstall failed.'
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
