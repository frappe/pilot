<template>
  <template v-if="status">
    <Button variant="outline" :theme="status.kind === 'failed' ? 'red' : 'gray'" @click="onClick">
      <template #prefix>
        <span class="size-4" :class="[status.icon, { 'animate-spin': status.kind === 'active' }]" />
      </template>
      {{ status.label }}
    </Button>
    <UpdateAppsDialog v-model="showDialog" />
  </template>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from 'frappe-ui'
import { useMigration } from '@/composables/migrations/useMigration'
import UpdateAppsDialog from '@/components/apps/UpdateAppsDialog.vue'

const router = useRouter()
const { status, start } = useMigration()
const showDialog = ref(false)

function onClick() {
  if (status.value.operationId) {
    router.push({ name: 'MigrationDetail', params: { operationId: status.value.operationId } })
  } else {
    showDialog.value = true
  }
}

onMounted(start)
</script>
