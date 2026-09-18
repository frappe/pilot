<script setup lang="ts">
import { Button } from 'frappe-ui'
import { useRouter } from 'vue-router'

import EmptyState from '@/components/common/EmptyState.vue'

const router = useRouter()

const openDatabaseSettings = () =>
  router.push({ name: 'Settings', params: { section: 'database' } })
</script>

<template>
  <EmptyState
    icon="lucide-gauge"
    title="Performance Schema is off"
    description="MariaDB collects no query or index statistics while it is off, so there is nothing to show here."
    :bordered="false"
    compact
    class="py-10"
  >
    <Button variant="subtle" size="sm" @click="openDatabaseSettings">
      Turn it on in Settings
    </Button>
  </EmptyState>
</template>
