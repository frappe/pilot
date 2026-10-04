<script setup lang="ts">
import { Button, Spinner } from 'frappe-ui'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import UpdateAppsDialog from '@/components/apps/UpdateAppsDialog.vue'

import { useUpdate } from '@/composables/updates/useUpdate'

const router = useRouter()
const { status, start } = useUpdate()
const showDialog = ref(false)

const onClick = () => {
  const current = status.value
  if (current && 'operationId' in current) {
    router.push({ name: 'UpdateDetail', params: { operationId: current.operationId } })
    return
  }

  showDialog.value = true
}

onMounted(start)
</script>

<template>
  <template v-if="status">
    <Button
      variant="outline"
      :theme="status.kind === 'failed' ? 'red' : 'gray'"
      class="order-first"
      @click="onClick"
    >
      <template #prefix>
        <Spinner v-if="status.kind === 'active'" size="md" />
        <span v-else class="size-4" :class="status.icon" />
      </template>
      {{ status.label }}
    </Button>

    <UpdateAppsDialog v-model="showDialog" />
  </template>
</template>
