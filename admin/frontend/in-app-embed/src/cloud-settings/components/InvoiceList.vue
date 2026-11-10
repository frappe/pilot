<script setup lang="ts">
import { getInvoicePdf } from '@frappe/cloud-sdk/api'
import type { Invoice } from '@frappe/cloud-sdk'
import { Badge, Button, ErrorMessage, TextInput, toast } from 'frappe-ui'
import { List, ListCell, ListHeader, ListHeaderCell, ListRow, ListRows } from 'frappe-ui/list'
import { computed, ref } from 'vue'
import PanelSkeleton from './PanelSkeleton.vue'
import { formatInvoiceMonth, type InvoicePayment } from '../invoicePayment'
import { useErrorMessage } from '../store'
import { useTranslation } from '../translation'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  invoices: Invoice[] | null
  error: string
  payment: InvoicePayment
}

const props = defineProps<Props>()
const emit = defineEmits<{ retry: [] }>()

const query = ref('')
const isShowingAll = ref(false)
const downloading = ref('')

const STATUS: Record<Invoice['status'], { label: string; theme: 'green' | 'amber' | 'red' | 'gray' }> = {
  Paid: { label: __('Paid'), theme: 'green' },
  Outstanding: { label: __('Unpaid'), theme: 'amber' },
  Overdue: { label: __('Overdue'), theme: 'red' },
  Waived: { label: __('Waived'), theme: 'gray' },
}

// Status folds under the month on a phone, so the amount and actions keep their room.
const columns = {
  base: ['minmax(0,1fr)', '5.5rem', '5.5rem'],
  sm: ['minmax(0,1fr)', '7rem', '6rem', '5.5rem'],
}

const rows = computed(() => {
  const term = query.value.trim().toLowerCase()

  return (props.invoices || [])
    .map((invoice) => ({ id: invoice.name, month: formatInvoiceMonth(invoice.period_start), invoice }))
    .filter(({ month, invoice }) =>
      [month, invoice.name, invoice.amount, STATUS[invoice.status].label].join(' ').toLowerCase().includes(term),
    )
})

// A year of invoices at first; a search always covers every invoice.
const PAGE_SIZE = 12
const visibleRows = computed(() => (isShowingAll.value || query.value.trim() ? rows.value : rows.value.slice(0, PAGE_SIZE)))
const hiddenCount = computed(() => rows.value.length - visibleRows.value.length)

const download = async (invoice: Invoice) => {
  downloading.value = invoice.name
  try {
    const pdf = await getInvoicePdf(invoice.name)
    const bytes = Uint8Array.from(atob(pdf.content), (character) => character.charCodeAt(0))
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }))
    link.download = pdf.filename
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (exception) {
    toast.error(getErrorMessage(exception))
  } finally {
    downloading.value = ''
  }
}
</script>

<template>
  <section class="space-y-3" aria-labelledby="invoices-heading">
    <h3 id="invoices-heading" class="text-base-semibold text-ink-gray-8">{{ __("Invoices") }}</h3>

    <TextInput
      v-if="invoices?.length"
      v-model="query"
      class="w-full sm:w-64"
      :aria-label="__('Search invoices')"
      :placeholder="__('Search')"
    >
      <template #prefix><span class="lucide-search size-4 text-ink-gray-5" aria-hidden="true" /></template>
    </TextInput>

    <PanelSkeleton v-if="!invoices && !error" kind="table" />

    <div v-else-if="error" class="flex flex-wrap items-center gap-3">
      <ErrorMessage :message="error" />
      <Button :label="__('Try again')" @click="emit('retry')" />
    </div>

    <div v-else-if="!invoices?.length" class="rounded-6 border border-dashed border-outline-gray-2 px-4 py-8 text-center">
      <p class="text-base-medium text-ink-gray-8">{{ __("No invoices yet") }}</p>
      <p class="mt-1 text-p-sm text-ink-gray-5">{{ __("Your first invoice arrives at the end of your first month.") }}</p>
    </div>

    <div v-else-if="!rows.length" class="rounded-6 border border-dashed border-outline-gray-2 px-4 py-8 text-center">
      <p class="text-p-sm text-ink-gray-6">{{ __("No invoices match “{0}”.", [query.trim()]) }}</p>
      <Button class="mt-3" :label="__('Clear search')" @click="query = ''" />
    </div>

    <List v-else class="list-row-px-2 sm:list-gap-6" :columns="columns" aria-labelledby="invoices-heading">
      <ListHeader>
        <ListHeaderCell>{{ __("Month") }}</ListHeaderCell>
        <ListHeaderCell class="justify-end">{{ __("Amount") }}</ListHeaderCell>
        <ListHeaderCell class="max-sm:hidden">{{ __("Status") }}</ListHeaderCell>
        <ListHeaderCell><span class="sr-only">{{ __("Actions") }}</span></ListHeaderCell>
      </ListHeader>

      <ListRows :items="visibleRows" v-slot="{ item: row }">
        <ListRow class="min-h-11 py-1.5">
          <ListCell class="flex-col !items-start justify-center">
            <span class="text-base text-ink-gray-8">{{ row.month }}</span>
            <span v-if="!row.invoice.has_pdf" class="text-p-xs text-ink-gray-5">{{ __("Shared with your other sites") }}</span>
            <Badge class="mt-1 sm:hidden" size="sm" :label="STATUS[row.invoice.status].label" :theme="STATUS[row.invoice.status].theme" />
          </ListCell>

          <ListCell class="justify-end text-base tabular-nums text-ink-gray-8">{{ row.invoice.amount }}</ListCell>

          <ListCell class="max-sm:hidden">
            <Badge size="sm" :label="STATUS[row.invoice.status].label" :theme="STATUS[row.invoice.status].theme" />
          </ListCell>

          <ListCell class="justify-end gap-1">
            <Button
              v-if="row.invoice.has_pdf"
              size="sm"
              variant="ghost"
              icon="lucide-download"
              :loading="downloading === row.invoice.name"
              :label="__('Download {0} invoice', [row.month])"
              :tooltip="__('Download PDF')"
              @click="download(row.invoice)"
            />
            <Button
              v-if="row.invoice.can_pay"
              size="sm"
              :loading="payment.opening === row.invoice.name"
              :label="__('Pay')"
              @click="payment.pay(row.invoice)"
            />
          </ListCell>
        </ListRow>
      </ListRows>
    </List>

    <Button
      v-if="hiddenCount"
      class="w-full"
      variant="ghost"
      :label="__('Show {0} older invoices', [hiddenCount])"
      @click="isShowingAll = true"
    />
  </section>
</template>
