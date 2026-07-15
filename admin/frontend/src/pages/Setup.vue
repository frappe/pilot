<template>
  <div class="flex justify-center items-center p-4 h-screen">
    <div class="flex flex-col bg-surface-base shadow-sm border rounded-xl border-outline-gray-2 w-full"
      :class="modalWidthClass" style="max-height: calc(100vh - 2rem)">

      <!-- Header -->
      <div class="px-5 py-4 border-b border-outline-gray-2">
        <p v-show="isConfiguring" class="mb-1 text-ink-gray-4 text-xs">
          Step {{ stepNumber }} of {{ stepSequence.length }}
        </p>
        <h1 class="font-semibold text-ink-gray-9 text-lg">{{ stepTitle }}</h1>
        <p v-show="stepSubtitle" class="mt-0.5 text-ink-gray-5 text-p-base">{{ stepSubtitle }}</p>
      </div>

      <div class="flex-1 p-5 overflow-y-auto">

        <!-- Loading -->
        <div v-show="currentStep === 'loading'" class="flex justify-center items-center py-10">
          <LoadingText />
        </div>

        <!-- Admin password -->
        <div v-show="currentStep === 'passwords'" class="flex flex-col gap-4">
          <div class="flex flex-col gap-2">
            <Password label="Admin password" v-model="adminPassword" placeholder="Choose a password"
              @keydown.enter="goToNextStep" />
            <PasswordStrengthMeter :password="adminPassword" />
          </div>
          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <!-- Database -->
        <div v-show="currentStep === 'database'" class="flex flex-col gap-4">
          <Select label="Database engine" v-model="dbType" :options="dbTypeOptions" />
          <Switch v-model="useExternalDb" label="Connect to an existing database server"
            description="Leave off to let pilot set up and manage its own user owned database server if not already present." />
          <div v-show="useExternalDb" class="flex gap-4">
            <TextInput class="flex-1" label="Host" v-model="dbHost" placeholder="db.example.com" />
            <TextInput class="w-28" label="Port" v-model="dbPort" :placeholder="dbPortPlaceholder" />
          </div>
          <TextInput v-show="showRootUsername" :label="useExternalDb ? 'Username' : 'Root username'" v-model="dbUser"
            :placeholder="rootUserPlaceholder" />
          <div>
            <Password :label="useExternalDb ? 'Password' : 'Root user password'" v-model="dbPassword"
              placeholder="password" autocomplete="off" data-lpignore="true" data-1p-ignore data-bwignore
              @keydown.enter="goToNextStep" />
            <p v-show="rootPasswordDescription" class="mt-1.5 text-ink-gray-6 text-p-sm">{{ rootPasswordDescription }}
            </p>
          </div>
          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <!-- Customize -->
        <div v-show="currentStep === 'customize'" class="flex flex-col gap-4">
          <Select label="Frappe branch" v-model="appBranch" :options="branchOptions" />
          <TextInput label="Frappe repository" v-model="appRepo" />
          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <!-- Installing -->
        <div v-show="isInstalling" class="flex flex-col gap-4">
          <p class="text-ink-gray-7 text-sm">{{ streamStatus }}</p>
          <button type="button" class="flex items-center self-start gap-1 text-ink-gray-5 hover:text-ink-gray-7 text-sm"
            @click="toggleStreamDetails">
            <FeatherIcon :name="showStreamDetails ? 'chevron-down' : 'chevron-right'" class="w-4 h-4" />
            {{ showStreamDetails ? 'Hide details' : 'Show details' }}
          </button>
          <div v-show="showStreamDetails">
            <TaskStream ref="terminal" :url="streamUrl" :guard-hidden-tab="true" @line="updateStreamStatus"
              @done="onStreamDone" @error="failInstall('Lost connection to the setup process.')" />
          </div>
          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <!-- Done: production hand-off already ran the production setup, so just wait for it to come up -->
        <div v-show="isDone && isProductionHandoff" class="flex flex-col justify-center items-center gap-3 py-10">
          <LoadingText />
          <p class="text-ink-gray-6 text-sm text-center">
            Finishing production setup. This page will reload automatically once your bench is live.
          </p>
        </div>

        <!-- Done: plain dev bench, production is a deliberate step the user runs later -->
        <div v-show="isDone && !isProductionHandoff" class="flex flex-col gap-4 py-2">
          <p class="text-ink-gray-7 text-sm">
            Your bench is ready. Run one of these in your terminal:
          </p>
          <div>
            <p class="font-medium text-ink-gray-6 text-xs">Develop locally</p>
            <code class="block bg-surface-gray-2 mt-1 px-2 py-1.5 rounded font-mono text-ink-gray-8 text-sm select-all">bench
      start</code>
          </div>
          <div>
            <p class="font-medium text-ink-gray-6 text-xs">Deploy to production</p>
            <code class="block bg-surface-gray-2 mt-1 px-2 py-1.5 rounded font-mono text-ink-gray-8 text-sm select-all">bench
      setup production --admin-domain &lt;your-domain&gt; --tls --letsencrypt-email
      &lt;you@example.com&gt;</code>
          </div>
          <p class="text-ink-gray-5 text-xs">
            <code class="font-mono">bench start</code> reloads this page automatically once the
            bench is back.
          </p>
        </div>
      </div>

      <!-- Footer -->
      <div v-show="isConfiguring || (isInstalling && errorMessage)" class="flex gap-2 px-5 py-4">
        <Button v-show="isInstalling && errorMessage" variant="subtle" class="w-full" @click="backToConfiguration">
          Back to configuration
        </Button>
        <Button v-show="isConfiguring && stepNumber > 1" variant="subtle" class="flex-1" @click="goToPreviousStep">
          Back
        </Button>
        <Button v-show="isConfiguring && currentStep === 'passwords'" variant="solid" class="w-full"
          :disabled="!isAdminPasswordValid" @click="goToNextStep">
          Next
        </Button>
        <Button v-show="isConfiguring && currentStep === 'database'" variant="solid" :loading="isSubmitting"
          class="flex-1" @click="goToNextStep">
          Verify credentials
        </Button>
        <Button v-show="isConfiguring && currentStep !== 'passwords' && currentStep !== 'database' && !isLastConfigStep"
          variant="solid" class="flex-1" @click="goToNextStep">
          Next
        </Button>
        <Button v-show="isConfiguring && currentStep !== 'passwords' && currentStep !== 'database' && isLastConfigStep"
          variant="solid" :loading="isSubmitting" class="flex-1" @click="startSetup">
          Set up bench
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  Button,
  Select,
  Switch,
  TextInput,
  FormLabel,
  Password,
  ErrorMessage,
  FeatherIcon,
  LoadingText,
} from 'frappe-ui'
import TaskStream from '../components/TaskStream.vue'
import PasswordStrengthMeter from '../components/PasswordStrengthMeter.vue'
import { useSetup } from '../composables/useSetup'

const {
  currentStep,
  errorMessage,
  isSubmitting,
  isLinux,
  isProductionHandoff,
  isDone,
  terminal,
  streamUrl,
  streamStatus,
  showStreamDetails,
  isAdminPasswordValid,
  adminPassword,
  dbType,
  dbUser,
  dbPassword,
  useExternalDb,
  dbHost,
  dbPort,
  dbPortPlaceholder,
  appRepo,
  appBranch,
  showRootUsername,
  rootUserPlaceholder,
  rootPasswordDescription,
  dbTypeOptions,
  branchOptions,
  stepSequence,
  stepNumber,
  isConfiguring,
  isInstalling,
  isLastConfigStep,
  modalWidthClass,
  stepTitle,
  stepSubtitle,
  goToNextStep,
  goToPreviousStep,
  startSetup,
  backToConfiguration,
  toggleStreamDetails,
  updateStreamStatus,
  onStreamDone,
  failInstall,
} = useSetup()
</script>
