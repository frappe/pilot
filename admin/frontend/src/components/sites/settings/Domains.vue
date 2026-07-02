<template>
  <div v-if="nginxEnabled">
    <p class="font-semibold text-ink-gray-8 text-base">Domains</p>
    <div v-if="loading" class="flex justify-center py-8">
      <LoadingText />
    </div>
    <template v-else>
      <div class="mt-1">
        <div v-for="row in domainRows" :key="row.domain"
          class="flex justify-between items-start gap-x-2.5 py-4 border-b border-outline-alpha-gray-1">
          <div class="flex items-start gap-2.5 min-w-0">
            <Tooltip :text="site?.ssl ? 'SSL active' : 'SSL inactive'">
              <span class="mt-0.5 size-4 text-ink-gray-5 shrink-0"
                :class="site?.ssl ? 'lucide-lock text-ink-green-6' : 'lucide-lock-open'" />
            </Tooltip>
            <div class="flex items-center gap-2 min-w-0">
              <p class="font-medium text-ink-gray-8 text-sm truncate">{{ row.domain }}</p>
              <Badge v-if="row.isPrimary" label="Primary" theme="green" size="sm" class="shrink-0" />
              <Badge v-else-if="row.isSite" label="Included" size="sm" class="shrink-0" />
            </div>
          </div>
          <Dropdown v-if="domainMenuOptions(row).length" :options="domainMenuOptions(row)" placement="bottom-end">
            <template #default="{ open }">
              <Button variant="ghost" size="sm" :active="open">
                <span class="size-4 lucide-ellipsis" />
              </Button>
            </template>
          </Dropdown>
        </div>
      </div>
      <ErrorMessage v-if="error" :message="error" class="mt-2" />
      <Button variant="subtle" size="sm" class="mt-4" @click="showAdd = true">
        <template #prefix><span class="size-4 lucide-plus" /></template>
        Use your own domain
      </Button>
    </template>
  </div>

  <AddDomainDialog v-model="showAdd" :site-name="siteName" @added="loadDomains" />
  <RemoveDomainDialog v-model="showRemove" :site-name="siteName" :domain="removeTarget" @removed="loadDomains" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Badge, Button, Dropdown, ErrorMessage, LoadingText, Tooltip } from 'frappe-ui'
import AddDomainDialog from './domains/AddDomainDialog.vue'
import RemoveDomainDialog from './domains/RemoveDomainDialog.vue'
import { useSite } from '@/composables/useSite'
import { sitesApi } from '@/api/sites'

const props = defineProps({ siteName: { type: String, required: true } })

const { site, nginxEnabled } = useSite(props.siteName)

const domains = ref([])
const primaryDomain = ref(null)
const loading = ref(false)
const error = ref('')

const domainRows = computed(() => {
  const rows = [{
    domain: props.siteName,
    isSite: true,
    isPrimary: !primaryDomain.value || primaryDomain.value === props.siteName,
  }]
  for (const domain of domains.value) {
    rows.push({ domain, isSite: false, isPrimary: primaryDomain.value === domain })
  }
  return rows
})

function domainMenuOptions(row) {
  const options = []
  if (!row.isPrimary) {
    options.push({ label: 'Make primary', icon: 'lucide-star', onClick: () => setPrimary(row.domain) })
    if (!row.isSite) {
      options.push({ label: 'Delete', icon: 'lucide-trash-2', theme: 'red', onClick: () => openRemove(row.domain) })
    }
  }
  return options
}

async function loadDomains() {
  loading.value = true
  error.value = ''
  try {
    const data = await sitesApi.domains.list(props.siteName)
    domains.value = data.domains || []
    primaryDomain.value = data.primary || null
  } catch (e) {
    error.value = e.message || 'Failed to load domains.'
  } finally {
    loading.value = false
  }
}

async function setPrimary(domain) {
  error.value = ''
  try {
    const data = await sitesApi.domains.setPrimary(props.siteName, domain)
    if (!data.ok) { error.value = data.error; return }
    await loadDomains()
  } catch (e) {
    error.value = e.message || 'Failed to set primary domain.'
  }
}

const showAdd = ref(false)
const showRemove = ref(false)
const removeTarget = ref('')

function openRemove(domain) {
  removeTarget.value = domain
  showRemove.value = true
}

onMounted(() => {
  if (nginxEnabled.value) loadDomains()
})

watch(nginxEnabled, (enabled) => {
  if (enabled) loadDomains()
})
</script>
