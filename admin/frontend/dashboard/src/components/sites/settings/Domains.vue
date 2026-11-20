<script setup lang="ts">
import {
  Alert,
  Badge,
  Button,
  Dropdown,
  type DropdownItem,
  ErrorMessage,
  LoadingText,
  Tooltip,
} from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import AddDomainDialog from '@/components/sites/settings/domains/AddDomainDialog.vue'
import RemoveDomainDialog from '@/components/sites/settings/domains/RemoveDomainDialog.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/sites/useSite'
import type { SiteDomain } from '@/types/siteDomains'
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

const dayMs = 86_400_000
const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

const certificateDays = (row: SiteDomain) => {
  const expires = row.tls_status?.expires_at

  return expires
    ? Math.max(0, Math.round((new Date(expires).getTime() - Date.now()) / dayMs))
    : null
}

const certificateTone = (row: SiteDomain) => {
  if (row.tls_status?.state === 'expired') return 'bg-surface-red-5'
  if (row.tls_status?.state === 'expiring') return 'bg-surface-amber-5'
  return 'bg-surface-green-5'
}

const lockIcon = (row: SiteDomain) => {
  if (isTlsBroken(row.tls_status)) return 'lucide-lock-open text-ink-red-5'
  return row.tls ? 'lucide-lock text-ink-green-5' : 'lucide-lock-open text-ink-gray-5'
}

const domainRows = computed(() => {
  return domains.value.map((route) => ({
    ...route,
    isSite: route.is_site,
    isPrimary: route.is_primary,
    days: certificateDays(route),
    endsOn: route.tls_status?.expires_at
      ? dateFormat.format(new Date(route.tls_status.expires_at))
      : '',
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

onMounted(() => loadDomains())

watch(nginxEnabled, () => loadDomains())

onUnmounted(() => {
  unmounted = true
  clearTimeout(pendingPoll)
})
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex items-center gap-2 h-7">
      <span class="size-4 text-ink-gray-5 lucide-globe" />
      <h2 class="text-base-medium text-ink-gray-8">Domains</h2>
      <span class="text-ink-gray-5 text-sm">{{ domainRows.length }}</span>

      <Button class="ml-auto" :disabled="!nginxEnabled" @click="showAdd = true">
        <template #prefix><span class="size-4 lucide-plus" /></template>
        Add domain
      </Button>
    </header>

    <LoadingText v-if="loading" class="justify-center py-8" />

    <template v-else>
      <div class="gap-3 grid sm:grid-cols-2 lg:grid-cols-3">
        <article
          v-for="row in domainRows"
          :key="row.domain"
          class="flex flex-col gap-3 p-4 border border-outline-gray-1.5 rounded-6 min-w-0"
        >
          <span class="flex items-center gap-2 min-w-0">
            <Tooltip :text="tlsTooltip(row.tls_status, row.tls, certificatePending)">
              <span class="size-4 shrink-0" :class="lockIcon(row)" />
            </Tooltip>

            <span class="flex-1 text-base-medium text-ink-gray-8 truncate">{{ row.domain }}</span>

            <Dropdown
              v-if="nginxEnabled && domainMenuOptions(row).length"
              :options="domainMenuOptions(row)"
            >
              <template #default="{ open }">
                <Button
                  variant="ghost"
                  class="-my-1 -mr-1.5"
                  :active="open"
                  icon="lucide-ellipsis"
                  label="Domain actions"
                  tooltip="Actions"
                />
              </template>
            </Dropdown>
          </span>

          <span class="flex items-center gap-1.5 h-5">
            <Badge v-if="row.isPrimary" label="Primary" theme="green" size="sm" />
            <Badge v-else-if="row.isSite" label="Included" size="sm" />

            <Badge
              v-if="tlsBadge(row.tls_status)"
              :label="tlsBadge(row.tls_status)?.label"
              :theme="tlsBadge(row.tls_status)?.theme"
              size="sm"
            />

            <Badge
              v-else-if="!row.tls && certificatePending"
              label="Certificate pending"
              theme="amber"
              size="sm"
            />
          </span>

          <p
            v-if="row.tls_status?.state === 'failed' && row.tls_status.error"
            class="text-ink-gray-6 text-p-xs break-words whitespace-pre-line"
          >
            {{ row.tls_status.error }}
          </p>

          <template v-else>
            <span class="bg-surface-gray-2 mt-auto rounded-full h-1.5 overflow-hidden">
              <span
                v-if="row.days !== null"
                class="block rounded-full h-full"
                :class="certificateTone(row)"
                :style="{ width: `${Math.min(100, (row.days / 90) * 100)}%` }"
              />
            </span>

            <span v-if="row.days !== null" class="flex justify-between text-ink-gray-5 text-xs">
              <span class="tabular-nums text-ink-gray-7">
                Certificate · {{ row.days }} days left
              </span>

              <span>ends {{ row.endsOn }}</span>
            </span>

            <span v-else class="text-ink-gray-5 text-xs">No certificate</span>
          </template>
        </article>
      </div>

      <Alert v-if="!nginxEnabled">
        <template #title>
          Custom domains need production. Run
          <span class="font-mono text-xs">pilot setup production</span>
          on the server.
        </template>
      </Alert>

      <ErrorMessage v-if="error" :message="error" />
    </template>
  </section>

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
