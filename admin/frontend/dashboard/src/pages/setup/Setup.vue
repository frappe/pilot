<script setup lang="ts">
import {
  Button,
  Combobox,
  Select,
  TextInput,
  FormLabel,
  Password,
  ErrorMessage,
  LoadingText,
} from 'frappe-ui'
import TaskStream from '@/components/tasks/TaskStream.vue'
import { useSetup } from '@/composables/setup/useSetup'

const {
  currentStep,
  errorMessage,
  isSubmitting,
  isLinux,
  isProductionHandoff,
  isDone,
  pilotCommand,
  terminal,
  streamUrl,
  streamStatus,
  showStreamDetails,
  dbType,
  dbUser,
  dbPassword,
  dbMode,
  dbModeOptions,
  dbHost,
  dbPort,
  dbPortPlaceholder,
  appRepo,
  appBranch,
  rootUserPlaceholder,
  dbTypeOptions,
  branchOptions,
  validatingFramework,
  frameworkIsValid,
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

<template>
  <div class="flex justify-center items-center p-4 h-screen">
    <div
      class="flex flex-col bg-surface-base shadow-sm border rounded-7 border-outline-gray-2 w-full"
      :class="modalWidthClass"
      style="max-height: calc(100vh - 2rem)"
    >
      <!-- Header -->
      <div class="px-5 py-4 border-b border-outline-gray-2">
        <p v-show="isConfiguring" class="mb-1 text-ink-gray-4 text-xs">
          Step {{ stepNumber }} of {{ stepSequence.length }}
        </p>

        <h1 class="text-lg-semibold">{{ stepTitle }}</h1>
        <p v-show="stepSubtitle" class="mt-0.5 text-ink-gray-5 text-p-base">{{ stepSubtitle }}</p>
      </div>

      <div class="flex-1 p-5 overflow-y-auto">
        <LoadingText v-show="currentStep === 'loading'" class="justify-center py-10" />

        <div v-show="currentStep === 'database'" class="flex flex-col gap-4">
          <Select label="Database engine" v-model="dbType" :options="dbTypeOptions" />
          <Select label="Database setup" v-model="dbMode" :options="dbModeOptions" />
          <template v-if="dbMode === 'external'">
            <div class="flex gap-4">
              <TextInput
                class="flex-1"
                label="Host"
                v-model="dbHost"
                placeholder="db.example.com"
              />
              <TextInput
                class="w-28"
                label="Port"
                v-model="dbPort"
                :placeholder="dbPortPlaceholder"
              />
            </div>

            <TextInput label="Root username" v-model="dbUser" :placeholder="rootUserPlaceholder" />
            <Password
              label="Root user password"
              v-model="dbPassword"
              placeholder="password"
              autocomplete="off"
              data-lpignore="true"
              data-1p-ignore
              data-bwignore
              @keydown.enter="goToNextStep"
            />
          </template>

          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <div v-show="currentStep === 'customize'" class="flex flex-col gap-4">
          <Combobox
            label="Frappe branch"
            v-model="appBranch"
            :options="branchOptions"
            trigger="button"
            placeholder="Search or type a branch…"
          >
            <template #item-typed-branch="{ query }">
              Use branch “{{ query }}”
            </template>
          </Combobox>
          <TextInput label="Frappe repository" v-model="appRepo" />
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
          <p v-else-if="validatingFramework" class="text-ink-gray-5 text-p-sm">
            Checking repository…
          </p>
          <p
            v-else-if="frameworkIsValid"
            class="flex items-center gap-1 text-ink-green-7 text-sm"
          >
            <span class="size-3.5 shrink-0 lucide-check" />
            Found frappe
          </p>
        </div>

        <div v-show="isInstalling" class="flex flex-col gap-4">
          <p class="text-ink-gray-7 text-sm">{{ streamStatus }}</p>
          <button
            type="button"
            class="flex items-center self-start gap-1 text-ink-gray-5 hover:text-ink-gray-7 text-sm"
            @click="toggleStreamDetails"
          >
            <span
              class="size-4"
              :class="showStreamDetails ? 'lucide-chevron-down' : 'lucide-chevron-right'"
            />
            {{ showStreamDetails ? 'Hide details' : 'Show details' }}
          </button>

          <TaskStream
            v-show="showStreamDetails"
            ref="terminal"
            :url="streamUrl"
            :guard-hidden-tab="true"
            @line="updateStreamStatus"
            @done="onStreamDone"
            @error="failInstall('Lost connection to the setup process.')"
          />

          <ErrorMessage v-show="errorMessage" :message="errorMessage" />
        </div>

        <!-- Done: production hand-off already ran the production setup, so just wait for it to come up -->
        <div
          v-show="isDone && isProductionHandoff"
          class="flex flex-col justify-center items-center gap-3 py-10"
        >
          <LoadingText />
          <p class="text-ink-gray-6 text-p-sm text-center">
            Finishing production setup. This page will reload automatically once your bench is live.
          </p>
        </div>

        <!-- Done: plain dev bench, production is a deliberate step the user runs later -->
        <div v-show="isDone && !isProductionHandoff" class="flex flex-col gap-4 py-2">
          <p class="text-ink-gray-7 text-p-sm">
            Your bench is ready. Run one of these in your terminal:
          </p>

          <div>
            <p class="font-medium text-ink-gray-6 text-xs">Develop locally</p>
            <code
              class="block bg-surface-gray-2 mt-1 px-2 py-1.5 rounded-4 font-mono text-ink-gray-8 text-sm select-all"
              >{{ pilotCommand }}
              start</code
            >
          </div>

          <div>
            <p class="font-medium text-ink-gray-6 text-xs">Deploy to production</p>
            <code
              class="block bg-surface-gray-2 mt-1 px-2 py-1.5 rounded-4 font-mono text-ink-gray-8 text-sm select-all"
              >{{ pilotCommand }}
              setup production --admin-domain &lt;your-domain&gt; --tls --letsencrypt-email
              &lt;you@example.com&gt;</code
            >
          </div>

          <p class="text-ink-gray-5 text-xs">
            <code class="font-mono">{{ pilotCommand }} start</code>
            reloads this page automatically once the bench is back.
          </p>
        </div>
      </div>

      <!-- Footer -->
      <div v-show="isConfiguring || (isInstalling && errorMessage)" class="flex gap-2 px-5 py-4">
        <Button
          v-show="isInstalling && errorMessage"
          class="w-full"
          @click="backToConfiguration"
        >
          Back to configuration
        </Button>

        <Button
          v-show="isConfiguring && stepNumber > 1"
          variant="subtle"
          class="flex-1"
          @click="goToPreviousStep"
        >
          Back
        </Button>

        <Button
          v-show="isConfiguring && currentStep === 'database' && dbMode === 'external'"
          variant="solid"
          :loading="isSubmitting"
          class="flex-1"
          @click="goToNextStep"
        >
          Verify credentials
        </Button>

        <Button
          v-show="isConfiguring && currentStep === 'database' && dbMode !== 'external'"
          variant="solid"
          class="flex-1"
          @click="goToNextStep"
        >
          Next
        </Button>

        <Button
          v-show="isConfiguring && currentStep !== 'database' && !isLastConfigStep"
          variant="solid"
          class="flex-1"
          @click="goToNextStep"
        >
          Next
        </Button>

        <Button
          v-show="isConfiguring && currentStep !== 'database' && isLastConfigStep"
          variant="solid"
          :loading="isSubmitting"
          :disabled="!frameworkIsValid"
          class="flex-1"
          @click="startSetup"
        >
          Set up bench
        </Button>
      </div>
    </div>
  </div>
</template>
