<script setup lang="ts">
import { Select } from 'frappe-ui'
import { ref } from 'vue'

import SiteInsights from '@/components/dashboard/SiteInsights.vue'

defineProps<{ siteName: string }>()

const windowOptions = [
  { value: '1h', label: '1 hour' },
  { value: '6h', label: '6 hours' },
  { value: '12h', label: '12 hours' },
  { value: '24h', label: '24 hours' },
  { value: '1w', label: '1 week' },
]

const window = ref('24h')
</script>

<template>
  <div class="flex flex-col gap-4">
    <Select v-model="window" :options="windowOptions" side="bottom" align="start" class="w-36" />
    <SiteInsights :site-name="siteName" :window="window" />
  </div>
</template>
