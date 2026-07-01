<template>
  <Teleport v-if="teleport" defer to="#header-actions">
    <Button variant="outline" :loading="checking" @click="onClick">
      <template #prefix>
        <span
          v-if="updatesAvailable"
          class="size-2 rounded-full bg-amber-500"
        />
        <span v-else class="size-4 lucide-refresh-cw" />
      </template>
      <span class="hidden sm:inline">{{ updatesAvailable ? 'Update available' : 'Check for updates' }}</span>
      <span class="sm:hidden">Updates</span>
    </Button>
  </Teleport>
  <Button v-else variant="outline" :loading="checking" @click="onClick">
    <template #prefix>
      <span
        v-if="updatesAvailable"
        class="size-2 rounded-full bg-amber-500"
      />
      <span v-else class="size-4 lucide-refresh-cw" />
    </template>
    {{ updatesAvailable ? 'Update available' : 'Check for updates' }}
  </Button>

  <UpdateAppsDialog v-model="showDialog" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button } from 'frappe-ui'
import { useAppUpdates } from '@/composables/useAppUpdates'
import UpdateAppsDialog from '@/components/UpdateAppsDialog.vue'

defineProps({
  teleport: { type: Boolean, default: true },
})

const { updatesAvailable, checking, checked, check } = useAppUpdates()
const showDialog = ref(false)

function onClick() {
  showDialog.value = true
  if (!checked.value) check()
}

onMounted(() => {
  if (!checked.value) check()
})
</script>
