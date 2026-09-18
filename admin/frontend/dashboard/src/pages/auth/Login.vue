<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Dialog, TextInput, ErrorMessage } from 'frappe-ui'

import PilotLogo from '@/components/icons/Pilot.vue'

import { apiErrorMessage } from '@/api/client'
import { authApi } from '@/api/auth'
import { useSession } from '@/composables/auth/useSession'
import { safeRedirect } from '@/utils/redirect'
import { useIsMobile } from '@/composables/common/useIsMobile'

// Off-SPA targets (the code editor) have no route here and need a full load.
const redirectAfterLogin = (router, value) => {
  const target = safeRedirect(value)
  if (router.resolve(target).matched.length) {
    router.replace(target)
  } else {
    window.location.assign(target)
  }
}

const route = useRoute()
const router = useRouter()
const { session, loadSession } = useSession()
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const showPassword = ref(false)
const showForgotPassword = ref(false)
const otp = ref('')
const twoFactorRequired = ref(false)
const isMobile = useIsMobile()

const cancelTwoFactor = () => {
  twoFactorRequired.value = false
  otp.value = ''
  errorMessage.value = ''
}

const login = async () => {
  if (twoFactorRequired.value ? !otp.value : !password.value) return
  isSubmitting.value = true
  errorMessage.value = ''
  try {
    const result = await authApi.login(password.value, otp.value)
    if (result.two_factor_required) {
      // Password accepted; the sign-in still needs a code.
      twoFactorRequired.value = true
      return
    }
    if (result.authenticated !== true) {
      errorMessage.value = apiErrorMessage(result, 'Login failed')
      return
    }
    await loadSession()
    redirectAfterLogin(router, route.query.redirect)
  } catch (e) {
    console.error(e)
    errorMessage.value = 'Could not reach the server'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="flex flex-col sm:justify-center items-center bg-surface-base p-4 sm:p-15 h-screen">
    <div class="flex flex-col items-start gap-5 p-6 w-full max-w-[371px]">
      <div class="flex flex-col gap-4">
        <PilotLogo class="size-8" />
        <div class="flex flex-col gap-1">
          <h1 class="text-lg-semibold">Sign In</h1>
          <p class="text-ink-gray-5 text-p-base">Welcome! Please sign in to continue.</p>
        </div>
      </div>

      <div class="flex flex-col gap-3 w-full">
        <TextInput
          v-if="!twoFactorRequired"
          v-model="password"
          label="Password"
          :type="showPassword ? 'text' : 'password'"
          placeholder="Enter password"
          autofocus
          @keydown.enter="login"
        >
          <template #prefix>
            <span class="size-4 text-ink-gray-5 lucide-lock" />
          </template>

          <template #suffix>
            <button
              type="button"
              tabindex="-1"
              class="text-ink-gray-5 hover:text-ink-gray-7"
              @click="showPassword = !showPassword"
            >
              <span v-if="showPassword" class="size-4 lucide-eye-off" />
              <span v-else class="size-4 lucide-eye" />
            </button>
          </template>
        </TextInput>

        <TextInput
          v-else
          v-model="otp"
          label="Authentication code"
          placeholder="123456"
          autofocus
          @keydown.enter="login"
        >
          <template #prefix>
            <span class="size-4 text-ink-gray-5 lucide-shield" />
          </template>
        </TextInput>

        <p v-if="twoFactorRequired" class="text-ink-gray-5 text-p-sm">
          Enter the code from an enrolled device, or one of your recovery codes.
        </p>

        <button
          v-else
          type="button"
          class="self-end text-ink-gray-6 text-p-sm hover:text-ink-gray-8 hover:underline"
          @click="showForgotPassword = true"
        >
          Forgot password?
        </button>

        <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        <Button variant="solid" :loading="isSubmitting" class="w-full" @click="login">
          {{ twoFactorRequired ? 'Verify' : 'Continue' }}
        </Button>

        <button
          v-if="twoFactorRequired"
          type="button"
          class="text-ink-gray-6 text-p-sm hover:text-ink-gray-8 hover:underline"
          @click="cancelTwoFactor"
        >
          Back
        </button>
      </div>
    </div>

    <p class="bottom-6 absolute text-ink-gray-3 text-xs">Frappe Bench Administrator</p>

    <Dialog
      v-model="showForgotPassword"
      title="Reset password"
      :position="isMobile ? 'top' : 'center'"
    >
      <ol class="space-y-2 pl-4 text-ink-gray-7 text-p-base list-decimal">
        <li>SSH into the server.</li>
        <li>
          Run
          <code class="bg-surface-gray-2 px-1 py-0.5 rounded-4 font-mono text-ink-gray-8"
            >pilot -b {{ session.benchName }} set-admin-password</code
          >
        </li>
      </ol>
    </Dialog>
  </div>
</template>
