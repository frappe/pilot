<script setup lang="ts">
import type { NextBill } from '@frappe/cloud-sdk'
import { List, ListCell, ListRow } from 'frappe-ui/list'
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  bill: NextBill
}

defineProps<Props>()
</script>

<template>
  <List class="w-full rounded-6 bg-surface-gray-1 px-3" :columns="['minmax(0,1fr)', '7rem']" :aria-label="__('Next payment breakdown')">
    <ListRow v-for="(line, index) in bill.lines" :key="index" class="py-2">
      <ListCell class="flex-col !items-start">
        <span class="text-base text-ink-gray-8">{{ line.item }}</span>
        <span v-if="line.detail" class="text-p-sm text-ink-gray-5">{{ line.detail }}</span>
      </ListCell>
      <ListCell class="justify-end text-base tabular-nums text-ink-gray-8">{{ line.amount }}</ListCell>
    </ListRow>

    <template v-if="bill.tax">
      <ListRow class="py-2 text-base text-ink-gray-6">
        <ListCell>{{ __("Subtotal") }}</ListCell>
        <ListCell class="justify-end tabular-nums">{{ bill.subtotal }}</ListCell>
      </ListRow>
      <ListRow class="py-2 text-base text-ink-gray-6">
        <ListCell>{{ bill.tax_type || __("Tax") }}</ListCell>
        <ListCell class="justify-end tabular-nums">{{ bill.tax }}</ListCell>
      </ListRow>
    </template>

    <ListRow class="py-2 text-base-semibold text-ink-gray-9">
      <ListCell>{{ __("Total") }}</ListCell>
      <ListCell class="justify-end tabular-nums">{{ bill.amount }}</ListCell>
    </ListRow>
  </List>
</template>
