<script setup lang="ts">
import { Badge, Button, Dialog, Dropdown, ErrorMessage, TextInput } from 'frappe-ui'
import { computed, inject, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Table from '../components/Table.vue'
import { openExternal } from '../external'
import type { Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const input = ref('')
const dnsRecords = ref([])
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

const domains = computed(() => {
  const rows = store.state.domains?.domains
  const routes = rows
    ?.filter((row) => typeof row.domain === 'object')
    .map(({ domain }) => ({ ...domain, is_default: domain.is_site }))

  return routes?.length ? routes : rows
})

const columns = [
  { label: __('Domain'), key: 'domain', class: 'w-1/2' },
  { label: __('Type'), key: 'type' },
  { label: __('SSL'), key: 'ssl' },
  { label: '', key: 'actions', class: 'w-12' },
]

const rows = computed(() =>
  (domains.value || []).map((domain) => ({ id: domain.domain, ...domain })),
)

const hasCustomDomains = computed(() => rows.value.some((row) => !row.is_default))

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

let dnsTimer

watch(normalizedDomain, (domain) => {
  clearTimeout(dnsTimer)
  dnsRecords.value = []

  if (!domain || domainError.value) return

  dnsTimer = setTimeout(() => loadDnsRecords(domain), 400)
})

const loadDnsRecords = async (domain) => {
  try {
    const response = await store.api.getDomainDnsRecords(domain)

    if (domain === normalizedDomain.value) dnsRecords.value = response.records || []
  } catch {}
}

const confirmAdd = async () => {
  const domain = normalizedDomain.value

  if (!canAdd.value) return

  await run(async () => {
    await store.api.addDomain(domain)

    input.value = ''

    await store.loadDomains(true)
  })
}

const urlFor = (row) => `${row.public_scheme || (row.tls ? 'https' : 'http')}://${row.domain}`

const menuOptions = (row) =>
  [
    !row.is_primary && {
      label: __('Make primary'),
      icon: 'lucide-star',
      onClick: () => makePrimary(row.domain),
    },
    !row.is_default && {
      label: __('Remove'),
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => askRemove(row.domain),
    },
  ].filter(Boolean)

const makePrimary = (domain) => {
  busyDomain.value = domain

  run(async () => {
    await store.api.setPrimaryDomain(domain)
    await store.loadDomains(true)
  })
}

const askRemove = (domain) => {
  removeTarget.value = domain
  showRemove.value = true
}

const confirmRemove = () => {
  showRemove.value = false
  busyDomain.value = removeTarget.value

  run(async () => {
    await store.api.removeDomain(removeTarget.value)
    await store.loadDomains(true)
  })
}

const run = async (action) => {
  working.value = true
  store.state.domainsError = ''

  try {
    await action()
  } catch (exception) {
    store.state.domainsError = store.api.getErrorMessage(exception)
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
    <div class="rounded-6 border border-outline-gray-2">
      <div class="grid items-end gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_auto]">
        <TextInput
          v-model="input"
          :label="__('Domain')"
          :placeholder="__('shop.example.com')"
          :disabled="working"
          @keydown.enter="confirmAdd"
        />

        <Button
          variant="solid"
          :disabled="!canAdd"
          :loading="working && !busyDomain"
          :label="__('Add')"
          @click="confirmAdd"
        />
      </div>

      <div
        v-if="domainError || error || dnsRecords.length"
        class="border-t border-outline-gray-2 px-4 py-3 text-p-sm text-ink-gray-5"
      >
        <ErrorMessage v-if="domainError || error" :message="domainError || error" />

        <template v-else-if="dnsRecords.length">
          <p>{{ __("Add these records at your DNS provider, then add the domain.") }}</p>

          <dl class="mt-2 grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] gap-x-6 gap-y-1">
            <template v-for="(record, index) in dnsRecords" :key="index">
              <dt class="text-ink-gray-8">{{ record.type }}</dt>
              <dd class="truncate text-ink-gray-7">{{ record.host }}</dd>
              <dd class="truncate font-mono text-ink-gray-7">{{ record.value }}</dd>
            </template>
          </dl>
        </template>
      </div>
    </div>

    <Table class="mt-6" :columns="columns" :rows="rows">
      <template #domain="{ row }">
        <a
          class="text-ink-gray-8 hover:underline"
          :href="urlFor(row)"
          target="_blank"
          rel="noopener"
          @click.prevent="openExternal(urlFor(row))"
        >
          {{ row.domain }}
        </a>

        <Badge v-if="row.is_primary" class="ml-2" theme="green" size="sm" :label="__('Primary')" />
      </template>

      <template #type="{ row }">
        <span class="text-ink-gray-6">
          {{ row.is_default ? __('Site address') : __('Custom domain') }}
        </span>
      </template>

      <template #ssl="{ row }">
        <span
          class="flex items-center gap-1.5"
          :class="row.tls ? 'text-ink-green-7' : 'text-ink-gray-5'"
        >
          <span
            :class="[row.tls ? 'lucide-lock' : 'lucide-lock-open', 'size-3.5']"
            aria-hidden="true"
          />
          {{ row.tls ? __('HTTPS') : __('HTTP only') }}
        </span>
      </template>

      <template #actions="{ row }">
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
      </template>
    </Table>
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
