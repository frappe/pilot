<script setup lang="ts">
import { useTranslation } from '../translation'
import { addDomain, getDomainDnsRecords, removeDomain, setPrimaryDomain } from '@frappe/cloud-sdk/api'
import type { DnsRecord, Domain } from '@frappe/cloud-sdk'
import { Badge, Button, Dialog, Dropdown, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { computed, inject, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Table from '../components/Table.vue'
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
const working = ref(false)
const loadingRecords = ref(false)
const showAdd = ref(false)
const addTarget = ref('')
const recordOptions = ref<DnsRecord[][]>([])
const verifying = ref(false)
const addError = ref('')
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

// A `.local` site name, such as Central's image site, is not reachable from outside.
const domains = computed(() =>
  store.state.domains?.domains.filter(
    (row) => row.is_primary || !(row.is_default && row.domain.endsWith('.local')),
  ),
)

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

const canContinue = computed(
  () =>
    Boolean(normalizedDomain.value) &&
    !domainError.value &&
    !working.value &&
    !loadingRecords.value,
)

// frappe without `options` lists Pilot's CNAME set, then its A set, in one list.
const toRecordOptions = (records: DnsRecord[]) => {
  const split = records.findIndex((record) => record.type.toUpperCase() === 'A')
  if (split < 0) return records.length ? [records] : []
  return [records.slice(0, split), records.slice(split)].filter((option) => option.length)
}

const optionType = (option: DnsRecord[]) =>
  option.find((record) => ['CNAME', 'A'].includes(record.type.toUpperCase()))?.type ?? option[0].type

const continueAdd = async () => {
  const domain = normalizedDomain.value

  if (!canContinue.value) return

  loadingRecords.value = true
  store.state.domainsError = ''

  try {
    const response = await getDomainDnsRecords(domain)

    recordOptions.value = response.options ?? toRecordOptions(response.records || [])
    addTarget.value = domain
    addError.value = ''
    showAdd.value = true
  } catch (exception) {
    store.state.domainsError = getErrorMessage(exception)
  } finally {
    loadingRecords.value = false
  }
}

const verifyAdd = async () => {
  verifying.value = true
  addError.value = ''

  try {
    await addDomain(addTarget.value)

    showAdd.value = false
    input.value = ''
    toast.success(__('{0} added.', [addTarget.value]))

    await store.loadDomains(true)
  } catch (exception) {
    addError.value = getErrorMessage(exception)
  } finally {
    verifying.value = false
  }
}

const recordColumns = [
  { key: 'type', label: __('Type') },
  { key: 'host', label: __('Host') },
  { key: 'value', label: __('Points to'), class: 'w-full', cellClass: 'font-mono' },
]

const recordRows = (option: DnsRecord[]) =>
  option.map((record, index) => ({ id: String(index), ...record }))

const copyValue = async (value: string) => {
  try {
    await navigator.clipboard.writeText(value)
    toast.success(__('Copied.'))
  } catch {
    toast.error(__("Couldn't copy. Select the text and copy it."))
  }
}

const urlFor = (row: Domain) => `${row.public_scheme || (row.tls ? 'https' : 'http')}://${row.domain}`

const menuOptions = (row: Domain) =>
  [
    !row.is_primary && {
      label: __('Make primary'),
      icon: 'lucide-star',
      onClick: () => makePrimary(row.domain),
    },
    !row.is_default && !row.is_primary && {
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
        @keydown.enter="continueAdd"
      >
        <template #prefix><span class="lucide-globe size-4 text-ink-gray-5" /></template>
      </TextInput>

      <Button
        variant="solid"
        :disabled="!normalizedDomain || Boolean(domainError) || working"
        :loading="loadingRecords"
        :label="__('Continue')"
        @click="continueAdd"
      />
    </div>

    <ErrorMessage v-if="domainError || error" class="mt-2" :message="domainError || error" />

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

  <Dialog v-model="showAdd" :title="__('Use your own domain')" size="xl">
    <template #default>
      <template v-if="recordOptions.length">
        <p class="text-p-base text-ink-gray-7">
          {{ recordOptions.length > 1
              ? __('Add either one of these records at your domain provider.')
              : __('Add this record at your domain provider.') }}
        </p>

        <div v-for="(option, index) in recordOptions" :key="index" class="mt-4">
          <p class="mb-2 text-sm text-ink-gray-7">
            {{ recordOptions.length > 1
                ? __('Option {0}: {1} record', [index + 1, optionType(option)])
                : __('{0} record', [optionType(option)]) }}
          </p>

          <Table
            class="border rounded-6 border-outline-gray-2"
            :columns="recordColumns"
            :rows="recordRows(option)"
          >
            <template #value="{ row }">
              <div class="flex items-center gap-2">
                <span class="truncate">{{ row.value }}</span>
                <Button
                  variant="ghost"
                  icon="lucide-copy"
                  :label="__('Copy {0}', [row.value])"
                  @click="copyValue(row.value)"
                />
              </div>
            </template>
          </Table>
        </div>

        <p class="mt-3 text-p-sm text-ink-gray-5">
          {{ __('DNS changes can take a few minutes to propagate.') }}
        </p>
      </template>

      <div v-else class="flex flex-col items-center gap-3 py-8">
        <div class="flex justify-center items-center bg-surface-green-2 rounded-full size-12">
          <span class="size-6 text-ink-green-2 lucide-check" aria-hidden="true" />
        </div>

        <p class="font-medium text-ink-gray-8">{{ __('No DNS records needed') }}</p>
      </div>

      <ErrorMessage v-if="addError" class="mt-3" :message="addError" />
    </template>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button :label="__('Cancel')" @click="showAdd = false" />

        <Button
          variant="solid"
          :loading="verifying"
          :label="recordOptions.length ? __('Verify DNS') : __('Register')"
          @click="verifyAdd"
        />
      </div>
    </template>
  </Dialog>

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
