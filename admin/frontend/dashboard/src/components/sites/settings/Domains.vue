<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Badge, Button, Dropdown, ErrorMessage, LoadingText, Tooltip } from 'frappe-ui'

import AddDomainDialog from '@/components/sites/settings/domains/AddDomainDialog.vue'
import RemoveDomainDialog from '@/components/sites/settings/domains/RemoveDomainDialog.vue'

import { useSite } from '@/composables/sites/useSite'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

interface Props {
  siteName: string
}

const props = defineProps<Props>()

const { site, nginxEnabled } = useSite(props.siteName)

const domains = ref([])
const primaryDomain = ref(null)
const loading = ref(false)
const error = ref('')

const domainRows = computed(() => {
  const rows = [
    {
      domain: props.siteName,
      isSite: true,
      isPrimary: !primaryDomain.value || primaryDomain.value === props.siteName,
    },
  ]
  for (const domain of domains.value) {
    rows.push({ domain, isSite: false, isPrimary: primaryDomain.value === domain })
  }
  return rows
})

const domainMenuOptions = (row) => {
  const options = []
  if (!row.isPrimary) {
    options.push({
      label: 'Make primary',
      icon: 'lucide-star',
      onClick: () => setPrimary(row.domain),
    })
    if (!row.isSite) {
      options.push({
        label: 'Delete',
        icon: 'lucide-trash-2',
        theme: 'red',
        onClick: () => openRemove(row.domain),
      })
    }
  }
  return options
}

const loadDomains = async () => {
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

const setPrimary = async (domain) => {
  error.value = ''
  try {
    const data = await sitesApi.domains.setPrimary(props.siteName, domain)
    if (!data.task_id) {
      error.value = apiErrorMessage(data, 'Failed to set primary domain.')
      return
    }
    await loadDomains()
  } catch (e) {
    error.value = e.message || 'Failed to set primary domain.'
  }
}

const showAdd = ref(false)
const showRemove = ref(false)
const removeTarget = ref('')

const openRemove = (domain) => {
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

<template>
  <div v-if="nginxEnabled">
    <h2 class="mb-3 text-base-semibold text-ink-gray-8">Domains</h2>
    <LoadingText v-if="loading" class="justify-center py-8" />

    <template v-else>
      <div
        v-for="row in domainRows"
        :key="row.domain"
        class="flex justify-between items-start gap-x-2.5 first:mt-1 py-4 border-b border-outline-alpha-gray-1"
      >
        <div class="flex items-start gap-2.5 min-w-0">
          <Tooltip :text="site?.ssl ? 'SSL active' : 'SSL inactive'">
            <span
              class="mt-0.5 size-4 text-ink-gray-5 shrink-0"
              :class="site?.ssl ? 'lucide-lock text-ink-green-5' : 'lucide-lock-open'"
            />
          </Tooltip>

          <div class="flex items-center gap-2 min-w-0">
            <p class="font-medium text-ink-gray-8 truncate">{{ row.domain }}</p>
            <Badge
              v-if="row.isPrimary"
              label="Primary"
              theme="green"
              size="sm"
              class="shrink-0"
            />
            <Badge v-else-if="row.isSite" label="Included" size="sm" class="shrink-0" />
          </div>
        </div>

        <Dropdown
          v-if="domainMenuOptions(row).length"
          :options="domainMenuOptions(row)"
        >
          <template #default="{ open }">
            <Button
              variant="ghost"
              :active="open"
              icon="lucide-ellipsis"
              label="Domain actions"
              tooltip="Actions"
            />
          </template>
        </Dropdown>
      </div>

      <ErrorMessage v-if="error" :message="error" class="mt-2" />
      <Button class="mt-4" @click="showAdd = true">
        <template #prefix><span class="size-4 lucide-plus" /></template>
        Use your own domain
      </Button>
    </template>
  </div>

  <AddDomainDialog v-model="showAdd" :site-name="siteName" @added="loadDomains" />
  <RemoveDomainDialog
    v-model="showRemove"
    :site-name="siteName"
    :domain="removeTarget"
    @removed="loadDomains"
  />
</template>
