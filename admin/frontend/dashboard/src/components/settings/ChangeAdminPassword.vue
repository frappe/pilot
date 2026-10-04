<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, ErrorMessage, toast } from 'frappe-ui'

import PasswordInput from '@/components/common/PasswordInput.vue'
import PasswordStrengthMeter from '@/components/common/PasswordStrengthMeter.vue'

import { settingsApi } from '@/api/settings'
import { meetsPasswordRequirements } from '@/utils/passwordStrength'
import { errorMessage } from '@/utils/error'

const emit = defineEmits(['passwordChanged'])

const newPassword = ref('')
const confirmPassword = ref('')
const saving = ref(false)
const error = ref('')

const canSubmit = computed(() => meetsPasswordRequirements(newPassword.value))

const validationError = () => {
  if (newPassword.value !== confirmPassword.value) return 'New passwords do not match.'
  return ''
}

const reset = () => {
  newPassword.value = ''
  confirmPassword.value = ''
}

const save = async () => {
  error.value = validationError()
  if (error.value) return

  saving.value = true
  try {
    await settingsApi.changeAdminPassword({ new_password: newPassword.value })
    reset()
    toast.success('Password changed')
    emit('passwordChanged')
  } catch (e) {
    error.value = errorMessage(e, 'Could not change the password.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="space-y-2">
      <PasswordInput
        v-model="newPassword"
        label="New password"
        placeholder="New password"
        autocomplete="new-password"
      />
      <PasswordStrengthMeter :password="newPassword" />
    </div>

    <PasswordInput
      v-model="confirmPassword"
      label="Confirm new password"
      placeholder="Repeat new password"
      autocomplete="new-password"
    />

    <ErrorMessage v-if="error" :message="error" />
    <div class="flex justify-end">
      <Button variant="solid" :loading="saving" :disabled="!canSubmit" @click="save">
        Change password
      </Button>
    </div>
  </div>
</template>
