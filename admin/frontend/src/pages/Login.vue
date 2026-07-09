<template>
  <div class="flex flex-col sm:justify-center items-center bg-surface-base p-4 sm:p-15 h-screen">
    <div class="flex flex-col items-start gap-5 p-6 w-full max-w-[371px]">
      <div class="flex flex-col gap-4">
        <PilotLogo class="size-8" />
        <div class="flex flex-col gap-1">
          <h1 class="font-semibold text-ink-gray-9 text-lg">{{ t('login.signIn') }}</h1>
          <p class="text-ink-gray-5 text-p-base">{{ t('login.welcome') }}</p>
        </div>
      </div>
      <div class="flex flex-col gap-3 w-full">
        <TextInput v-model="password" :label="t('login.password')" :type="showPassword ? 'text' : 'password'"
          :placeholder="t('login.enterPassword')" autofocus @keydown.enter="login">
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
          {{ t('login.forgotPassword') }}
        </button>
        <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        <Button variant="solid" :loading="isSubmitting" class="w-full" @click="login">
          {{ t('common.continue') }}
        </Button>
      </div>
    </div>

    <div class="bottom-6 absolute flex flex-col items-center gap-3">
      <FormControl v-model="languageModel" type="select" :options="languages" class="w-36" />
      <p class="text-ink-gray-3 text-xs">{{ t('app.footer') }}</p>
    </div>

    <Dialog v-model="showForgotPassword" :options="resetPasswordOptions" :position="isMobile ? 'top' : 'center'">
      <template #body-content>
        <ol class="space-y-2 pl-4 text-ink-gray-7 text-p-base list-decimal">
          <li>{{ t('login.sshInstruction') }}</li>
          <li>
            {{ t('login.runCommand') }}
            <code
              class="bg-surface-gray-2 px-1 py-0.5 rounded font-mono text-ink-gray-8">bench -b {{ session.benchName }} set-admin-password</code>
          </li>
        </ol>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, FormControl, TextInput } from 'frappe-ui'
import LucideLock from '~icons/lucide/lock'
import LucideEye from '~icons/lucide/eye'
import LucideEyeOff from '~icons/lucide/eye-off'
import PilotLogo from '@/components/PilotLogo.vue'
import { authApi } from '../api/auth'
import { useSession } from '../composables/useSession'
import { safeRedirect } from '../utils/redirect'
import { useIsMobile } from '../composables/useIsMobile'
import { useI18n } from '../i18n'

const route = useRoute()
const router = useRouter()
const { session, loadSession } = useSession()
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)
const showPassword = ref(false)
const showForgotPassword = ref(false)
const isMobile = useIsMobile()
const { currentLanguage, languages, setLanguage, t } = useI18n()
const languageModel = computed({
  get: () => currentLanguage.value,
  set: setLanguage,
})
const resetPasswordOptions = computed(() => ({ title: t('login.resetPassword') }))

async function login() {
  if (!password.value) return
  isSubmitting.value = true
  errorMessage.value = ''
  try {
    const result = await authApi.login(password.value)
    if (!result.ok) {
      errorMessage.value = result.error || t('login.loginFailed')
      return
    }
    await loadSession()
    router.replace(safeRedirect(route.query.redirect))
  } catch (e) {
    console.error(e)
    errorMessage.value = t('login.couldNotReachServer')
  } finally {
    isSubmitting.value = false
  }
}
</script>
