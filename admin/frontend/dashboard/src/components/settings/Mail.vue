<script setup lang="ts">
import { Button, Checkbox, ErrorMessage, FormControl, Spinner, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

import { settingsApi } from '@/api/settings'
import { errorMessage } from '@/utils/error'

const ADDRESS_RE = /^[^@\s]+@[^@\s]+$/

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const server = ref('')
const port = ref('')
const email = ref('')
const login = ref('')
const password = ref('')
const passwordSet = ref(false)
const useSsl = ref(false)
const loginIsDifferent = ref(false)

const savedPayload = ref('')
const dirty = computed(() => JSON.stringify(buildPayload()) !== savedPayload.value)

// Blank port means "whatever the encryption mode defaults to", so the field can
// be left alone; the placeholder shows which one that is.
const defaultPort = computed(() => (useSsl.value ? 465 : 587))

const buildPayload = () => ({
  server: server.value.trim(),
  port: Number(port.value) || 0,
  email: email.value.trim(),
  login: loginIsDifferent.value ? login.value.trim() : '',
  password: password.value,
  use_ssl: useSsl.value,
})

const portError = computed(() => {
  if (!port.value) return ''
  const value = Number(port.value)
  if (!Number.isInteger(value) || value < 1 || value > 65535)
    return 'Port must be a number between 1 and 65535.'
  return ''
})

const emailError = computed(() => {
  if (email.value.trim() && !ADDRESS_RE.test(email.value.trim())) return 'Must be an email address.'
  return ''
})

// Everything the server needs, so Save stays dead until the check could pass.
const canSave = computed(
  () =>
    Boolean(server.value.trim()) &&
    Boolean(email.value.trim()) &&
    !emailError.value &&
    !portError.value &&
    (passwordSet.value || Boolean(password.value)),
)

const save = async () => {
  if (!canSave.value) return

  error.value = ''
  saving.value = true
  try {
    await settingsApi.update({ mail: buildPayload() })
    passwordSet.value = passwordSet.value || Boolean(password.value)
    password.value = ''
    savedPayload.value = JSON.stringify(buildPayload())
    toast.success('Mail settings saved')
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    const saved = data.mail || {}
    server.value = saved.server || ''
    port.value = saved.port ? String(saved.port) : ''
    email.value = saved.email || ''
    login.value = saved.login || ''
    loginIsDifferent.value = Boolean(saved.login)
    useSsl.value = Boolean(saved.use_ssl)
    passwordSet.value = Boolean(saved.password_set)
    savedPayload.value = JSON.stringify(buildPayload())
  } catch (e) {
    error.value = errorMessage(e, 'Could not load settings.')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="flex flex-col gap-6">
    <section class="space-y-5">
      <div class="flex flex-col gap-1">
        <p class="text-base-medium text-ink-gray-8">Mailbox</p>
        <p class="text-ink-gray-6 text-p-sm">The account alerts and your sites send email from.</p>
      </div>

      <div class="space-y-4">
        <FormControl
          label="Email address"
          required
          type="text"
          v-model="email"
          placeholder="alerts@example.com"
          :error="emailError"
        />

        <div class="gap-4 grid sm:grid-cols-[1fr_8rem]">
          <FormControl
            label="Outgoing server"
            required
            type="text"
            v-model="server"
            placeholder="smtp.example.com"
          />

          <FormControl
            label="Port"
            type="text"
            v-model="port"
            :placeholder="String(defaultPort)"
            :error="portError"
          />
        </div>

        <!-- Checkbox is inline-flex by default, so the column has to come from here. -->
        <div class="flex flex-col items-start gap-3">
          <Checkbox v-model="useSsl" label="Use SSL instead of STARTTLS" />
          <Checkbox v-model="loginIsDifferent" label="Use a different login name" />
        </div>

        <FormControl
          v-if="loginIsDifferent"
          label="Login name"
          type="text"
          v-model="login"
          placeholder="alerts"
        />

        <FormControl
          label="Password"
          :required="!passwordSet"
          type="password"
          autocomplete="new-password"
          v-model="password"
          :placeholder="passwordSet ? 'Unchanged' : 'SMTP password'"
        />
      </div>
    </section>

    <ErrorMessage v-if="error" :message="error" />

    <Button
      class="self-end"
      variant="solid"
      :loading="saving"
      :disabled="!dirty || !canSave"
      @click="save"
    >
      Save changes
    </Button>
  </div>
</template>
