<template>
  <div class="flex flex-col sm:justify-center items-center bg-surface-base p-4 sm:p-15 h-screen">
    <div class="flex flex-col items-start gap-5 p-6 w-full max-w-[371px]">
      <div class="flex flex-col gap-4">
        <PilotLogo class="size-8" />
        <div class="flex flex-col gap-1">
          <h1 class="font-semibold text-ink-gray-9 text-lg">Sign In</h1>
          <p class="text-ink-gray-5 text-p-base">Welcome! Please sign in to continue.</p>
        </div>
      </div>
      <div class="flex flex-col gap-3 w-full">
        <TextInput v-model="password" label="Password" :type="showPassword ? 'text' : 'password'"
          placeholder="Enter password" autofocus @keydown.enter="login">
          <template #prefix>
            <LucideLock class="size-4 text-ink-gray-5" />
          </template>
          <template #suffix>
            <button type="button" tabindex="-1" class="text-ink-gray-5 hover:text-ink-gray-7"
              @click="showPassword = !showPassword">
              <LucideEyeOff v-if="showPassword" class="size-4" />
              <LucideEye v-else class="size-4" />
            </button>
          </template>
        </TextInput>
        <button type="button" class="self-end text-ink-gray-6 text-p-sm hover:text-ink-gray-8 hover:underline"
          @click="showForgotPassword = true">
          Forgot password?
        </button>
        <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        <Button variant="solid" :loading="isSubmitting" class="w-full" @click="login">
          Continue
        </Button>
      </div>
    </div>

    <p class="bottom-6 absolute text-ink-gray-3 text-xs">Frappe Bench Administrator</p>

    <Dialog v-model="showForgotPassword" :options="{ title: 'Reset password' }" :position="isMobile ? 'top' : 'center'">
      <template #body-content>
        <ol class="space-y-2 pl-4 text-ink-gray-7 text-p-base list-decimal">
          <li>SSH into the server.</li>
          <li>
            Run
            <code
              class="bg-surface-gray-2 px-1 py-0.5 rounded font-mono text-ink-gray-8">bench -b {{ session.benchName }} set-admin-password</code>
          </li>
        </ol>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Dialog, TextInput, ErrorMessage } from 'frappe-ui'
import LucideLock from '~icons/lucide/lock'
import LucideEye from '~icons/lucide/eye'
import LucideEyeOff from '~icons/lucide/eye-off'
import PilotLogo from '@/components/common/PilotLogo.vue'
import { apiErrorMessage } from '../../api/client'
import { authApi } from '../../api/auth'
import { useSession } from '../../composables/auth/useSession'
import { safeRedirect } from '../../utils/redirect'
import { useIsMobile } from '../../composables/common/useIsMobile'

const route = useRoute()
const router = useRouter()
const { session, loadSession } = useSession()
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const showPassword = ref(false)
const showForgotPassword = ref(false)
const isMobile = useIsMobile()

async function login() {
  if (!password.value) return
  isSubmitting.value = true
  errorMessage.value = ''
  try {
    const result = await authApi.login(password.value)
    if (result.authenticated !== true) {
      errorMessage.value = apiErrorMessage(result, 'Login failed')
      return
    }
    await loadSession()
    router.replace(safeRedirect(route.query.redirect))
  } catch (e) {
    console.error(e)
    errorMessage.value = 'Could not reach the server'
  } finally {
    isSubmitting.value = false
  }
}
</script>
