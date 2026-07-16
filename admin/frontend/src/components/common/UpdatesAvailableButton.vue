<template>
  <Teleport v-if="teleport && updatesAvailable" defer to="#header-actions">
    <Button variant="outline" class="order-first" @click="onClick">
      <template #prefix>
        <span class="size-4 lucide-circle-arrow-up" />
      </template>
      <span class="hidden sm:inline">Update available</span>
      <span class="sm:hidden">Updates</span>
    </Button>
  </Teleport>
  <Button v-else-if="updatesAvailable" variant="outline" @click="onClick">
    <template #prefix>
      <span class="size-4 lucide-circle-arrow-up" />
    </template>
    Update available
  </Button>

  <UpdateAppsDialog v-model="showDialog" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button } from 'frappe-ui'
import { useAppUpdates } from '@/composables/apps/useAppUpdates'
import UpdateAppsDialog from '@/components/apps/UpdateAppsDialog.vue'

defineProps({
  teleport: { type: Boolean, default: true },
})

const { updatesAvailable, checked, check } = useAppUpdates()
const showDialog = ref(false)

function onClick() {
  showDialog.value = true
  if (!checked.value) check()
}

onMounted(() => {
  if (!checked.value) check()
})
</script>
