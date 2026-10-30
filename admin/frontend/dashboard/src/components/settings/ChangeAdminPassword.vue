<script setup lang="ts">
import { Button, ErrorMessage, toast } from 'frappe-ui'
import { computed, ref } from 'vue'
import { settingsApi } from '@/api/settings'
import PasswordInput from '@/components/common/PasswordInput.vue'
import PasswordStrengthMeter from '@/components/common/PasswordStrengthMeter.vue'
import { errorMessage } from '@/utils/error'
import { meetsPasswordRequirements } from '@/utils/passwordStrength'

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
  <div class="space-y-5">
    <div class="flex justify-between items-center gap-3">
      <div class="flex flex-col gap-1">
        <p class="text-base-medium text-ink-gray-8">Change password</p>
        <p class="text-ink-gray-6 text-p-sm">Update the password that signs in to this bench.</p>
      </div>

      <Button
        variant="solid"
        label="Update"
        :loading="saving"
        :disabled="!canSubmit"
        @click="save"
      />
    </div>

    <div class="items-start gap-4 grid sm:grid-cols-2">
      <div class="space-y-2">
        <PasswordInput
          v-model="newPassword"
          label="New password"
          required
          placeholder="New password"
          autocomplete="new-password"
        />
        <PasswordStrengthMeter :password="newPassword" />
      </div>

      <PasswordInput
        v-model="confirmPassword"
        label="Confirm new password"
        required
        placeholder="Repeat new password"
        autocomplete="new-password"
      />
    </div>

    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>
