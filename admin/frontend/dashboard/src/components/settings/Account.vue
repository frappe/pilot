<script setup lang="ts">
import { Button, Dialog, toast } from 'frappe-ui'
import { ref } from 'vue'
import { sessionApi } from '@/api/session'
import ChangeAdminPassword from '@/components/settings/ChangeAdminPassword.vue'
import TwoFactor from '@/components/settings/TwoFactor.vue'
import { errorMessage } from '@/utils/error'

const showRevokePrompt = ref(false)
const revoking = ref(false)

const revokeOtherSessions = async () => {
  revoking.value = true
  try {
    const result = await sessionApi.revokeAll()
    const others = Math.max((result.revoked_sessions || 0) - 1, 0)
    toast.success(
      others
        ? `${others} other session${others === 1 ? '' : 's'} signed out`
        : 'No other sessions to revoke',
    )
    showRevokePrompt.value = false
  } catch (e) {
    toast.error(errorMessage(e, 'Could not revoke other sessions.'))
  } finally {
    revoking.value = false
  }
}
</script>

<template>
  <div class="flex flex-col gap-8">
    <ChangeAdminPassword @passwordChanged="showRevokePrompt = true" />

    <section class="pt-8 border-t border-outline-alpha-gray-1">
      <TwoFactor />
    </section>
  </div>

  <Dialog v-model="showRevokePrompt" title="Password changed" size="md">
    <p class="text-ink-gray-7 text-p-base">
      Revoke every other active session? Anyone signed in elsewhere will be signed out immediately —
      this browser stays signed in.
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
