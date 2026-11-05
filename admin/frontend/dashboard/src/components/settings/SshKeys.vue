<script setup lang="ts">
import { Button, Dialog, ErrorMessage, Spinner, Textarea, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { apiErrorMessage } from '@/api/client'
import { sshKeysApi } from '@/api/sshKeys'
import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import SettingsIllustration from '@/components/settings/SettingsIllustration.vue'
import type { AuthorizedSSHKey } from '@/types/sshKeys'
import { errorMessage } from '@/utils/error'

interface KeyRow {
  fingerprint: string
  label: string
}

const columns = [
  { label: 'Name', key: 'label', class: 'w-1/3' },
  { label: 'Fingerprint', key: 'fingerprint' },
  { label: '', key: 'actions', class: 'w-12' },
]

const loading = ref(true)
const adding = ref(false)
const error = ref('')
const loadError = ref('')
const keys = ref<AuthorizedSSHKey[]>([])
const newKey = ref('')
const showAdd = ref(false)
const showRemove = ref(false)
const removing = ref<KeyRow | null>(null)
const removingBusy = ref(false)

const rows = computed(() =>
  keys.value.map((k) => ({ fingerprint: k.fingerprint, label: k.comment || 'Unnamed key' })),
)
const isLastKey = computed(() => rows.value.length <= 1)

const load = async () => {
  loading.value = true
  loadError.value = ''
  try {
    keys.value = (await sshKeysApi.list()).keys || []
  } catch (e) {
    loadError.value = errorMessage(e, 'Could not load SSH keys.')
  } finally {
    loading.value = false
  }
}

const openAdd = () => {
  newKey.value = ''
  error.value = ''
  showAdd.value = true
}

const add = async () => {
  adding.value = true
  error.value = ''
  try {
    const result = await sshKeysApi.add(newKey.value.trim())
    if (result.fingerprint) {
      showAdd.value = false
      toast.success('Key added')
      await load()
    } else {
      error.value = apiErrorMessage(result, 'Could not add key.')
    }
  } catch (e) {
    error.value = errorMessage(e, 'Could not add key.')
  } finally {
    adding.value = false
  }
}

const promptRemove = (row: KeyRow) => {
  removing.value = row
  showRemove.value = true
}

const confirmRemove = async () => {
  const target = removing.value
  if (!target) return

  removingBusy.value = true
  try {
    const response = await sshKeysApi.remove(target.fingerprint)
    if (response.ok) {
      toast.success('Key removed')
      showRemove.value = false
      await load()
    } else {
      toast.error(apiErrorMessage(await response.json(), 'Could not remove key.'))
    }
  } catch (e) {
    toast.error(errorMessage(e, 'Could not remove key.'))
  } finally {
    removingBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-5">
    <div class="flex justify-between items-center gap-3">
      <div class="flex flex-col gap-1">
        <p class="text-base-medium text-ink-gray-8">Authorized keys</p>
        <p class="text-ink-gray-6 text-p-sm">Public keys that can SSH into this server.</p>
      </div>

      <Button icon-left="lucide-plus" label="Add key" @click="openAdd" />
    </div>

    <div
      v-if="loadError"
      class="py-12 border border-dashed rounded-7 border-outline-red-2 text-ink-red-2 text-p-sm text-center"
    >
      {{ loadError }}
    </div>

    <EmptyState
      compact
      v-else-if="!rows.length"
      title="No SSH keys"
      description="Add a public key to let its owner SSH into this server."
    >
      <template #illustration>
        <SettingsIllustration kind="ssh-keys" />
      </template>
    </EmptyState>
    <Table v-else :columns="columns" :rows="rows" height="max-h-96">
      <template #actions="{ row }">
        <Button
          variant="ghost"
          theme="red"
          icon="lucide-trash-2"
          label="Remove SSH key"
          tooltip="Remove SSH key"
          @click="promptRemove(row)"
        />
      </template>
    </Table>
  </div>

  <Dialog v-model="showAdd" title="Add SSH key" size="md">
    <Textarea
      label="Public key"
      required
      v-model="newKey"
      :rows="3"
      placeholder="ssh-ed25519 AAAA… user@host"
    />
    <ErrorMessage v-if="error" :message="error" class="mt-2" />
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showAdd = false">Cancel</Button>
        <Button variant="solid" :loading="adding" :disabled="!newKey.trim()" @click="add">
          Add key
        </Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showRemove" title="Remove SSH key" size="md">
    <p v-if="isLastKey" class="text-ink-gray-7 text-p-base">
      This is the last authorized key. It can't be removed, or you'd lose SSH access to this server.
    </p>

    <p v-else class="text-ink-gray-7 text-p-base">
      Remove <span class="font-semibold text-ink-gray-8 break-all">{{ removing?.label }}</span>?
      Whoever holds the matching private key loses SSH access.
    </p>

    <template #actions>
      <div v-if="!isLastKey" class="flex justify-end gap-2">
        <Button variant="ghost" @click="showRemove = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="removingBusy" @click="confirmRemove"
          >Remove</Button
        >
      </div>
    </template>
  </Dialog>
</template>
