<template>
  <div class="flex h-screen items-center justify-center bg-surface-base p-4">
    <div
      class="flex w-full flex-col rounded-xl border border-outline-gray-2 bg-surface-base shadow-sm"
      :class="modalWidthClass"
      style="max-height: calc(100vh - 2rem)"
    >
      <div class="border-b border-outline-gray-2 px-5 py-4">
        <p v-if="isConfiguring" class="mb-1 text-xs text-ink-gray-4">
          Step {{ stepNumber }} of {{ stepSequence.length }}
        </p>
        <h1 class="font-medium text-ink-gray-7">{{ stepTitle }}</h1>
        <p v-if="stepSubtitle" class="mt-0.5 text-sm text-ink-gray-4">{{ stepSubtitle }}</p>
      </div>

      <div class="flex-1 overflow-y-auto p-5">
        <div v-if="currentStep === 'loading'" class="flex items-center justify-center py-10">
          <FeatherIcon name="loader" class="h-5 w-5 animate-spin text-ink-gray-4" />
        </div>

        <div v-else-if="currentStep === 'passwords'" class="flex flex-col gap-4">
          <Password
            label="Admin password"
            v-model="form.admin_password"
            placeholder="Choose a password"
            @keydown.enter="goToNextStep"
          />
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        </div>

        <div v-else-if="currentStep === 'database'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Database engine"
            v-model="form.db_type"
            :options="[
              { label: 'MariaDB', value: 'mariadb' },
              { label: 'PostgreSQL', value: 'postgres' },
            ]"
          />

          <template v-if="form.db_type === 'mariadb'">
            <FormControl
              v-if="isLinux"
              type="select"
              label="MariaDB setup"
              v-model="form.dedicated_db"
              :options="[
                { label: 'Dedicated instance (recommended)', value: 'dedicated' },
                { label: 'Shared system MariaDB', value: 'shared' },
              ]"
            />
            <FormControl
              v-if="(!isLinux || form.dedicated_db === 'shared') && !mariadbWillInstall"
              label="MariaDB admin user"
              v-model="form.mariadb_admin_user"
            />
            <Password
              label="MariaDB root password"
              v-model="form.mariadb_password"
              placeholder="password"
              :description="mariadbPasswordDescription"
              @keydown.enter="goToNextStep"
            />
          </template>

          <template v-else>
            <FormControl
              v-if="isLinux && !isAlpine"
              type="select"
              label="PostgreSQL setup"
              v-model="form.dedicated_db"
              :options="[
                { label: 'Dedicated cluster (recommended)', value: 'dedicated' },
                { label: 'Shared system PostgreSQL', value: 'shared' },
              ]"
            />
            <FormControl label="PostgreSQL superuser" v-model="form.postgres_admin_user" />
            <Password
              label="PostgreSQL password"
              v-model="form.postgres_password"
              placeholder="password"
              :description="postgresPasswordDescription"
              @keydown.enter="goToNextStep"
            />
          </template>
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        </div>

        <div v-else-if="currentStep === 'customize'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Frappe branch"
            v-model="form.app_branch"
            :options="branchOptions"
          />
          <FormControl label="Frappe repository" v-model="form.app_repo" />
          <FormControl
            v-if="isLinux && form.dedicated_db === 'dedicated'"
            type="checkbox"
            label="Use volumes (snapshots & backups)"
            v-model="form.volume_enabled"
          />
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        </div>

        <div v-else-if="currentStep === 'storage'" class="flex flex-col gap-4">
          <FormControl
            type="select"
            label="Store data on"
            v-model="form.volume_backing"
            :options="[
              { label: 'This machine\'s disk', value: 'image' },
              { label: 'An attached disk', value: 'device' },
            ]"
          />
          <FormControl
            v-if="form.volume_backing === 'device' && showDeviceDropdown"
            type="select"
            label="Attached disk"
            v-model="form.volume_device"
            :options="deviceOptions"
          />
          <FormControl
            v-else-if="form.volume_backing === 'device'"
            label="Attached disk"
            v-model="form.volume_device"
            placeholder="/dev/sdb"
          />
          <div v-else class="space-y-1.5">
            <div class="flex items-baseline justify-between">
              <FormLabel label="Space to allocate" />
              <span class="text-sm text-ink-gray-5"
                >{{ imageSizeGiB }} GB of {{ freeGiB }} GB free</span
              >
            </div>
            <Slider
              v-model="imageSliderModel"
              :min="imageSizeMinGiB"
              :max="imageSizeMaxGiB"
              :step="1"
            />
          </div>
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        </div>

        <div v-else-if="isInstalling" class="flex flex-col gap-4">
          <p class="text-sm text-ink-gray-7">{{ streamStatus }}</p>
          <button
            type="button"
            class="flex items-center gap-1 self-start text-sm text-ink-gray-5 hover:text-ink-gray-7"
            @click="toggleStreamDetails"
          >
            <FeatherIcon
              :name="showStreamDetails ? 'chevron-down' : 'chevron-right'"
              class="h-4 w-4"
            />
            {{ showStreamDetails ? 'Hide details' : 'Show details' }}
          </button>
          <div v-show="showStreamDetails">
            <TaskStream
              ref="terminal"
              :url="streamUrl"
              :guard-hidden-tab="true"
              @line="updateStreamStatus"
              @done="onStreamDone"
              @error="failInstall('Lost connection to the setup process.')"
            />
          </div>
          <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        </div>

        <div v-else-if="currentStep === 'done'" class="flex flex-col gap-4 py-2">
          <p class="text-sm text-ink-gray-7">
            Your bench is ready. Run one of these in your terminal:
          </p>
          <div>
            <p class="text-xs font-medium text-ink-gray-6">Develop locally</p>
            <code
              class="mt-1 block rounded bg-surface-gray-2 px-2 py-1.5 font-mono text-sm text-ink-gray-8 select-all"
              >bench start</code
            >
          </div>
          <div>
            <p class="text-xs font-medium text-ink-gray-6">Deploy to production</p>
            <code
              class="mt-1 block rounded bg-surface-gray-2 px-2 py-1.5 font-mono text-sm text-ink-gray-8 select-all"
              >bench setup production --admin-domain &lt;your-domain&gt; --tls --letsencrypt-email
              &lt;you@example.com&gt;</code
            >
          </div>
          <p class="text-xs text-ink-gray-5">
            <code class="font-mono">bench start</code> reloads this page automatically once the
            bench is back.
          </p>
        </div>
      </div>

      <div
        v-if="
          (!isInstalling && currentStep !== 'done' && currentStep !== 'loading') ||
          (isInstalling && errorMessage)
        "
        class="flex gap-2 border-t border-outline-gray-2 px-5 py-4"
      >
        <Button
          v-if="isInstalling && errorMessage"
          variant="subtle"
          class="w-full"
          @click="backToConfiguration"
        >
          Back to configuration
        </Button>
        <template v-else>
          <Button v-if="stepNumber > 1" variant="subtle" class="flex-1" @click="goToPreviousStep">
            Back
          </Button>
          <Button
            v-if="currentStep === 'passwords'"
            variant="solid"
            class="w-full"
            @click="goToNextStep"
          >
            Next
          </Button>
          <Button
            v-else-if="currentStep === 'database'"
            variant="solid"
            :loading="isSubmitting"
            class="flex-1"
            @click="goToNextStep"
          >
            Next
          </Button>
          <Button
            v-else-if="!isLastConfigStep"
            variant="solid"
            class="flex-1"
            @click="goToNextStep"
          >
            Next
          </Button>
          <Button v-else variant="solid" :loading="isSubmitting" class="flex-1" @click="startSetup">
            Set up bench
          </Button>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  Button,
  FormControl,
  FormLabel,
  Password,
  Slider,
  ErrorMessage,
  FeatherIcon,
} from 'frappe-ui'
import TaskStream from '../components/TaskStream.vue'
import { useSetup } from '../composables/useSetup'

const {
  form,
  currentStep,
  errorMessage,
  isSubmitting,
  isLinux,
  isAlpine,
  terminal,
  streamUrl,
  streamStatus,
  showStreamDetails,
  mariadbWillInstall,
  mariadbPasswordDescription,
  postgresPasswordDescription,
  branchOptions,
  deviceOptions,
  showDeviceDropdown,
  freeGiB,
  imageSizeGiB,
  imageSizeMinGiB,
  imageSizeMaxGiB,
  imageSliderModel,
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
