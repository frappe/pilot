<script setup lang="ts">
import { useTranslation } from '../translation'
import { addDomain, getDomainDnsRecords, removeDomain, setPrimaryDomain } from '@frappe/cloud-sdk/api'
import type { DnsRecord, Domain } from '@frappe/cloud-sdk'
import { Badge, Button, Dialog, Dropdown, ErrorMessage, TextInput } from 'frappe-ui'
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const input = ref('')
const dnsRecords = ref<DnsRecord[]>([])
const working = ref(false)
const removeTarget = ref('')
const showRemove = ref(false)
const busyDomain = ref('')
const overlayTarget = inject('overlayTarget', 'body')

watch(
  () => props.active,
  (active) => {
    if (active) store.loadDomains()
  },
  { immediate: true },
)

const domains = computed(() => store.state.domains?.domains)

const hasCustomDomains = computed(() => (domains.value || []).some((row) => !row.is_default))

const error = computed(() => store.state.domainsError)
const loadFailed = computed(() => Boolean(error.value) && !domains.value)
const domainPattern = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/

const normalizedDomain = computed(() =>
  input.value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/[/?#].*$/, '')
    .replace(/\.$/, ''),
)

const domainError = computed(() => {
  const domain = normalizedDomain.value

  if (!domain) return ''
  if (!domainPattern.test(domain)) return __('Enter a valid domain, like shop.example.com.')
  if ((domains.value || []).some((row) => row.domain === domain)) {
    return __('{0} is already added.', [domain])
  }

  return ''
})

const canAdd = computed(
  () => Boolean(normalizedDomain.value) && !domainError.value && !working.value,
)

let dnsTimer: ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(() => clearTimeout(dnsTimer))

watch(normalizedDomain, (domain) => {
  clearTimeout(dnsTimer)
  dnsRecords.value = []

  if (!domain || domainError.value) return

  dnsTimer = setTimeout(() => loadDnsRecords(domain), 400)
})

const loadDnsRecords = async (domain: string) => {
  try {
    const response = await getDomainDnsRecords(domain)

    if (domain === normalizedDomain.value) dnsRecords.value = response.records || []
  } catch {}
}

const confirmAdd = async () => {
  const domain = normalizedDomain.value

  if (!canAdd.value) return

  await run(async () => {
    await addDomain(domain)

    input.value = ''

    await store.loadDomains(true)
  })
}

const urlFor = (row: Domain) => `${row.public_scheme || (row.tls ? 'https' : 'http')}://${row.domain}`

const menuOptions = (row: Domain) =>
  [
    !row.is_primary && {
      label: __('Make primary'),
      icon: 'lucide-star',
      onClick: () => makePrimary(row.domain),
    },
    !row.is_default && {
      label: __('Remove'),
      icon: 'lucide-trash-2',
      theme: 'red' as const,
      onClick: () => askRemove(row.domain),
    },
  ].filter((item) => item !== false)

const makePrimary = (domain: string) => {
  busyDomain.value = domain

  run(async () => {
    await setPrimaryDomain(domain)
    await store.loadDomains(true)
  })
}

const askRemove = (domain: string) => {
  removeTarget.value = domain
  showRemove.value = true
}

const confirmRemove = () => {
  showRemove.value = false
  busyDomain.value = removeTarget.value

  run(async () => {
    await removeDomain(removeTarget.value)
    await store.loadDomains(true)
  })
}

const run = async (action: () => Promise<void>) => {
  working.value = true
  store.state.domainsError = ''

  try {
    await action()
  } catch (exception) {
    store.state.domainsError = getErrorMessage(exception)
  } finally {
    working.value = false
    busyDomain.value = ''
  }
}
</script>

<template>
  <Panel
    :title="__('Domains')"
    :description="__('The addresses this site answers on.')"
    :loading="!domains && !error"
    :error="loadFailed ? error : ''"
    :error-title="__(`Couldn't load domains`)"
    @retry="store.loadDomains(true)"
  >
    <div class="flex gap-2">
      <TextInput
        v-model="input"
        class="flex-1"
        :aria-label="__('Domain')"
        :placeholder="__('Enter domain here')"
        :disabled="working"
        @keydown.enter="confirmAdd"
      >
        <template #prefix><span class="lucide-globe size-4 text-ink-gray-5" /></template>
      </TextInput>

      <Button
        variant="solid"
        :disabled="Boolean(domainError)"
        :loading="working && !busyDomain"
        :label="__('Add')"
        @click="confirmAdd"
      />
    </div>

    <ErrorMessage v-if="domainError || error" class="mt-2" :message="domainError || error" />

    <div v-else-if="dnsRecords.length" class="mt-3 bg-surface-gray-2 p-4 rounded-6 text-p-sm text-ink-gray-6">
      <p>{{ __("Add these records at your DNS provider, then add the domain.") }}</p>

      <dl class="mt-3 grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] gap-x-6 gap-y-1.5 font-mono text-xs">
        <template v-for="(record, index) in dnsRecords" :key="index">
          <dt class="text-ink-gray-8">{{ record.type }}</dt>
          <dd class="truncate text-ink-gray-7">{{ record.host }}</dd>
          <dd class="truncate font-mono text-ink-gray-7">{{ record.value }}</dd>
        </template>
      </dl>
    </div>

    <ul v-if="domains?.length" class="gap-3 grid sm:grid-cols-2 mt-4">
      <li
        v-for="row in domains"
        :key="row.domain"
        class="group flex items-center gap-3 dark:bg-surface-gray-1 p-4 border dark:border-transparent rounded-6 border-outline-gray-1.5"
      >
        <span
          class="grid place-items-center rounded-6 size-9 shrink-0"
          :class="row.tls ? 'bg-surface-green-2 text-ink-green-7' : 'bg-surface-gray-2 text-ink-gray-5'"
        >
          <span :class="[row.tls ? 'lucide-lock-keyhole' : 'lucide-lock-keyhole-open', 'size-4']" aria-hidden="true" />
        </span>

        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 min-w-0">
            <a
              class="group/link flex items-center gap-1 min-w-0 text-base-medium text-ink-gray-8"
              :href="urlFor(row)"
              target="_blank"
              rel="noopener"
              @click.prevent="openExternal(urlFor(row))"
            >
              <span class="truncate">{{ row.domain }}</span>
              <span
                class="lucide-arrow-up-right size-3.5 shrink-0 text-ink-gray-5 opacity-0 group-hover/link:opacity-100 group-focus-visible/link:opacity-100 transition-opacity"
                aria-hidden="true"
              />
            </a>

            <Badge v-if="row.is_primary" theme="green" size="sm" :label="__('Primary')" />
          </div>

          <p class="flex mt-1.5 text-sm text-ink-gray-5 leading-none whitespace-nowrap divide-x divide-outline-gray-3">
            <span class="pr-2">{{ row.is_default ? __('Site address') : __('Custom domain') }}</span>
            <span class="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pl-2 transition-opacity">
              {{ row.tls ? __('HTTPS') : __('HTTP only') }}
            </span>
          </p>
        </div>

        <Dropdown
          v-if="menuOptions(row).length"
          align="end"
          :portal-to="overlayTarget"
          :options="menuOptions(row)"
        >
          <Button
            variant="ghost"
            icon="lucide-ellipsis"
            :loading="busyDomain === row.domain"
            :disabled="working && busyDomain !== row.domain"
            :label="__('Actions for {0}', [row.domain])"
          />
        </Dropdown>
      </li>
    </ul>
  </Panel>

  <Dialog v-model="showRemove" :title="__('Remove domain')" size="md">
    <template #default>
      <p class="text-p-base text-ink-gray-7">
        {{ __("{0} will stop pointing to this site. You can add it again later.", [removeTarget]) }}
      </p>
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="showRemove = false" />

        <Button variant="solid" theme="red" :label="__('Remove')" @click="confirmRemove" />
      </div>
    </template>
  </Dialog>
</template>
