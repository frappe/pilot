<script setup lang="ts">
import { ref } from 'vue'
import { Button, Dialog, toast } from 'frappe-ui'

import SettingsRow from '@/components/settings/SettingsRow.vue'
import ChangeAdminPassword from '@/components/settings/ChangeAdminPassword.vue'
import TwoFactor from '@/components/settings/TwoFactor.vue'
import Firewall from '@/components/settings/Firewall.vue'
import Waf from '@/components/settings/Waf.vue'
import SshKeys from '@/components/settings/SshKeys.vue'

import { SECURITY_SECTIONS as sections } from '@/components/settings/sections'
import { sessionApi } from '@/api/session'

const openSection = defineModel<{ id: string } | null>('openSection')

const showRevokePrompt = ref(false)
const revoking = ref(false)

const revokeOtherSessions = async () => {
  revoking.value = true
  try {
    const result = await sessionApi.revokeAll()
    const others = Math.max((result.revoked_sessions || 0) - 1, 0)
    toast.success(others ? `${others} other session${others === 1 ? '' : 's'} signed out` : 'No other sessions to revoke')
    showRevokePrompt.value = false
  } catch (e) {
    toast.error(e.message || 'Could not revoke other sessions.')
  } finally {
    revoking.value = false
  }
}
</script>

<template>
  <ChangeAdminPassword
    v-if="openSection?.id === 'password'"
    @passwordChanged="openSection = null; showRevokePrompt = true"
  />
  <TwoFactor v-else-if="openSection?.id === 'two-factor'" />
  <Firewall v-else-if="openSection?.id === 'firewall'" />
  <Waf v-else-if="openSection?.id === 'waf'" />
  <SshKeys v-else-if="openSection?.id === 'ssh-keys'" />

  <div v-else class="-mx-2.5 divide-y divide-outline-alpha-gray-1 hover-merges-dividers">
    <SettingsRow
      v-for="section in sections"
      :key="section.id"
      as="button"
      interactive
      :label="section.label"
      :description="section.description"
      @click="openSection = section"
    >
      <span class="size-4 text-ink-gray-5 lucide-chevron-right" aria-hidden="true" />
    </SettingsRow>
  </div>

  <Dialog v-model="showRevokePrompt" title="Password changed" size="md">
    <p class="text-ink-gray-7 text-p-base">
      Revoke every other active session? Anyone signed in elsewhere will be signed out
      immediately — this browser stays signed in.
    </p>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" :disabled="revoking" @click="showRevokePrompt = false">
          Not now
        </Button>

        <Button variant="solid" theme="red" :loading="revoking" @click="revokeOtherSessions">
          Revoke other sessions
        </Button>
      </div>
    </template>
  </Dialog>
</template>
