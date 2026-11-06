<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Badge,
  Button,
  Dropdown,
  type DropdownItem,
  ErrorMessage,
  LoadingText,
  Tooltip,
} from 'frappe-ui'

import AddDomainDialog from '@/components/sites/settings/domains/AddDomainDialog.vue'
import RemoveDomainDialog from '@/components/sites/settings/domains/RemoveDomainDialog.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import type { SiteDomain } from '@/types/siteDomains'
import { useSite } from '@/composables/sites/useSite'
import { errorMessage } from '@/utils/error'
import { isTlsBroken, tlsBadge, tlsTooltip } from '@/utils/tlsStatus'

interface Props {
  siteName: string
}

interface DomainRow extends SiteDomain {
  isSite: boolean
  isPrimary: boolean
}

const props = defineProps<Props>()

const { nginxEnabled, reload } = useSite(props.siteName)

const domains = ref<SiteDomain[]>([])
const primaryDomain = ref('')
const certificateEmailRequired = ref(false)
const certificatePending = ref(false)
const loading = ref(false)
const error = ref('')

const domainRows = computed(() => {
  return domains.value.map((route) => ({
    ...route,
    isSite: route.is_site,
    isPrimary: route.is_primary,
  }))
})

const domainMenuOptions = (row: DomainRow): DropdownItem[] => {
  const options: DropdownItem[] = []
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

const PENDING_POLL_MS = 5000
let pendingPoll: ReturnType<typeof setTimeout> | undefined
let unmounted = false

const loadDomains = async (quiet = false) => {
  clearTimeout(pendingPoll)
  loading.value = !quiet
  error.value = ''
  try {
    const data = await sitesApi.domains.list(props.siteName)
    domains.value = data.domains || []
    primaryDomain.value = data.primary || ''
    certificateEmailRequired.value = Boolean(data.certificate_email_required)
    certificatePending.value = Boolean(data.certificate_pending)
    if (certificatePending.value && !unmounted) {
      pendingPoll = setTimeout(() => loadDomains(true), PENDING_POLL_MS)
    }
  } catch (e) {
    error.value = errorMessage(e, 'Failed to load domains.')
  } finally {
    loading.value = false
  }
}

const setPrimary = async (domain: string) => {
  error.value = ''
  try {
    const data = await sitesApi.domains.setPrimary(props.siteName, domain)
    if (!data.task_id) {
      error.value = apiErrorMessage(data, 'Failed to set primary domain.')
      return
    }
    await Promise.all([loadDomains(), reload()])
  } catch (e) {
    error.value = errorMessage(e, 'Failed to set primary domain.')
  }
}

const showAdd = ref(false)
const showRemove = ref(false)
const removeTarget = ref('')

const openRemove = (domain: string) => {
  removeTarget.value = domain
  showRemove.value = true
}

onMounted(() => {
  if (nginxEnabled.value) loadDomains()
})

watch(nginxEnabled, (enabled) => {
  if (enabled) loadDomains()
})

onUnmounted(() => {
  unmounted = true
  clearTimeout(pendingPoll)
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
          <Tooltip :text="tlsTooltip(row.tls_status, row.tls, certificatePending)">
            <span
              class="mt-0.5 size-4 text-ink-gray-5 shrink-0"
              :class="
                isTlsBroken(row.tls_status)
                  ? 'lucide-lock-open text-ink-red-5'
                  : row.tls
                    ? 'lucide-lock text-ink-green-5'
                    : 'lucide-lock-open'
              "
            />
          </Tooltip>

          <div class="flex flex-col gap-1 min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <p class="font-medium text-ink-gray-8 truncate">{{ row.domain }}</p>
              <Badge v-if="row.isPrimary" label="Primary" theme="green" size="sm" class="shrink-0" />
              <Badge v-else-if="row.isSite" label="Included" size="sm" class="shrink-0" />
              <Badge
                v-if="tlsBadge(row.tls_status)"
                :label="tlsBadge(row.tls_status)?.label"
                :theme="tlsBadge(row.tls_status)?.theme"
                size="sm"
                class="shrink-0"
              />
              <Badge
                v-else-if="!row.tls && certificatePending"
                label="Certificate pending"
                theme="amber"
                size="sm"
                class="shrink-0"
              />
            </div>
            <p
              v-if="row.tls_status?.state === 'failed' && row.tls_status.error"
              class="text-p-sm text-ink-gray-6 whitespace-pre-line break-words"
            >
              {{ row.tls_status.error }}
            </p>
          </div>
        </div>

        <Dropdown v-if="domainMenuOptions(row).length" :options="domainMenuOptions(row)">
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

  <AddDomainDialog
    v-model="showAdd"
    :site-name="siteName"
    :email-required="certificateEmailRequired"
    @added="loadDomains"
  />
  <RemoveDomainDialog
    v-model="showRemove"
    :site-name="siteName"
    :domain="removeTarget"
    @removed="loadDomains"
  />
</template>
