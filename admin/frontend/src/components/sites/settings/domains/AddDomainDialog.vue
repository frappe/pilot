<template>
  <Dialog v-model="show" :options="{ title: 'Use your own domain', size: 'lg' }">
    <template #body-content>
      <template v-if="step === 'input'">
        <p class="text-ink-gray-7 text-sm">
          To add a custom domain, you must already own it. If you don't have one, buy it and come back here.
        </p>
        <TextInput v-model="domain" placeholder="www.example.com" class="mt-4 w-full" @keydown.enter="continueAdd">
          <template #label>
            <span class="text-sm">Domain</span>
          </template>
        </TextInput>
        <ErrorMessage v-if="error" :message="error" class="mt-2" />
        <div class="flex justify-end gap-2 mt-4">
          <Button variant="subtle" @click="show = false">Cancel</Button>
          <Button variant="solid" :loading="continuing" :disabled="!domain.trim()" @click="continueAdd">
            Continue
          </Button>
        </div>
      </template>
      <template v-else>
        <template v-if="dnsRecordGroups.length">
          <p class="text-ink-gray-7 text-sm">
            <template v-if="dnsRecordGroups.length > 1">
              Add <span class="font-medium text-ink-gray-8">either one</span> of these records at your domain
              provider.
            </template>
            <template v-else>Add this record at your domain provider.</template>
          </p>
          <div v-for="(group, i) in dnsRecordGroups" :key="group.type" class="mt-3">
            <p class="font-medium text-ink-gray-7 text-sm">
              {{ dnsRecordGroups.length > 1 ? `Option ${i + 1}: ${group.type} record` : `${group.type} record` }}
            </p>
            <SimpleTable class="mt-2" :columns="DNS_RECORD_COLUMNS" :rows="group.records" />
          </div>
          <p class="mt-3 text-ink-gray-5 text-xs">DNS changes can take a few minutes to propagate.</p>
        </template>
        <div v-else class="flex flex-col items-center gap-3 py-8">
          <div class="flex justify-center items-center bg-surface-green-2 rounded-full size-12">
            <span class="size-6 text-ink-green-3 lucide-check" />
          </div>
          <p class="font-medium text-ink-gray-8 text-base">No DNS records needed</p>
        </div>
        <ErrorMessage v-if="error" :message="error" class="mt-2" />
        <div class="flex justify-end gap-2 mt-4">
          <Button variant="subtle" @click="show = false">Cancel</Button>
          <Button variant="solid" :loading="adding" @click="confirmAdd">
            {{ dnsRecordGroups.length ? 'Verify DNS' : 'Register' }}
          </Button>
        </div>
      </template>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Button, Dialog, ErrorMessage, TextInput } from 'frappe-ui'
import SimpleTable from '@/components/SimpleTable.vue'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'

const DNS_RECORD_COLUMNS = [
  { key: 'type', label: 'Type' },
  { key: 'host', label: 'Host' },
  { key: 'value', label: 'Points to' },
]

const props = defineProps({ siteName: { type: String, required: true } })
const emit = defineEmits(['added'])

const show = defineModel({ default: false })

const step = ref('input')
const domain = ref('')
const error = ref('')
const continuing = ref(false)
const adding = ref(false)
const dnsRecordGroups = ref([])

// Reset to a blank first step each time the dialog opens.
watch(show, (open) => {
  if (!open) return
  step.value = 'input'
  domain.value = ''
  error.value = ''
  dnsRecordGroups.value = []
})

function toRecordGroups(records) {
  const groups = []
  if (records?.cname?.length) groups.push({ type: 'CNAME', records: records.cname })
  if (records?.a?.length) groups.push({ type: 'A', records: records.a })
  return groups
}

async function continueAdd() {
  const value = domain.value.trim()
  if (!value) return
  error.value = ''
  continuing.value = true
  try {
    const data = await sitesApi.domains.dnsRecords(props.siteName, value)
    if (data.error) { error.value = apiErrorMessage(data, 'Could not generate DNS records.'); return }
    dnsRecordGroups.value = toRecordGroups(data)
    step.value = 'records'
  } catch (e) {
    error.value = e.message || 'Failed to validate domain.'
  } finally {
    continuing.value = false
  }
}

async function confirmAdd() {
  error.value = ''
  adding.value = true
  try {
    const data = await sitesApi.domains.add(props.siteName, domain.value.trim())
    if (!data.task_id) { error.value = apiErrorMessage(data, 'Failed to add domain.'); return }
    show.value = false
    emit('added')
  } catch (e) {
    error.value = e.message || 'Failed to add domain.'
  } finally {
    adding.value = false
  }
}
</script>
