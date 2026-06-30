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

  <Dialog v-model="showDialog" :options="{ title: 'Bench Update', size: 'md' }">
    <template #body-content>
      <p class="text-sm text-ink-gray-6">
        <template v-if="updatesAvailable">
          App updates are available for this bench.
        </template>
        <template v-else-if="checked">Your bench is up to date.</template>
        <template v-else>Checking for updates…</template>
      </p>
      <div class="mt-5 flex justify-end gap-2 border-t border-outline-gray-1 pt-4">
        <Button variant="ghost" @click="showDialog = false">Close</Button>
        <Button
          v-if="updatesAvailable"
          variant="solid"
          :loading="checking"
          @click="check"
        >
          Update Now
        </Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button, Dialog } from 'frappe-ui'
import { useAppUpdates } from '@/composables/useAppUpdates'

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
