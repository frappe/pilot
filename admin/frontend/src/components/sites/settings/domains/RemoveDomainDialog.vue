<template>
  <Dialog v-model="show" :options="{ title: 'Remove domain', size: 'md' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Remove <span class="font-semibold text-ink-gray-8 break-all">{{ domain }}</span> from this site? It will
        stop serving this domain.
      </p>
      <ErrorMessage v-if="error" :message="error" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="show = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="removing" @click="confirmRemove">Remove</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Button, Dialog, ErrorMessage } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

const props = defineProps({
  siteName: { type: String, required: true },
  domain: { type: String, default: '' },
})
const emit = defineEmits(['removed'])

const show = defineModel({ default: false })

const error = ref('')
const removing = ref(false)

watch(show, (open) => { if (open) error.value = '' })

async function confirmRemove() {
  error.value = ''
  removing.value = true
  try {
    const data = await sitesApi.domains.remove(props.siteName, props.domain)
    if (!data.task_id) { error.value = apiErrorMessage(data, 'Failed to remove domain.'); return }
    show.value = false
    emit('removed')
  } catch (e) {
    error.value = e.message || 'Failed to remove domain.'
  } finally {
    removing.value = false
  }
}
</script>
