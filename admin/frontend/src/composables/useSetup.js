import { ref, computed, watch, onMounted } from 'vue'
import { useSetupHandoff } from './useSetupHandoff'
import { useVolumeStorage } from './useVolumeStorage'
import { setupApi } from '../api/setup'

const STEP_TITLES = {
  passwords: 'Admin password',
  database: 'Database',
  customize: 'Customize your bench',
  storage: 'Storage',
  running: 'Setting up your bench',
  done: 'Setup complete',
}
const STEP_SUBTITLES = {
  database: 'Choose and configure your database',
  storage: 'Choose where your bench keeps its data',
}

function defaultForm() {
  return {
    admin_password: '',
    db_type: 'mariadb',
    mariadb_password: '',
    mariadb_admin_user: 'root',
    dedicated_db: 'dedicated',
    postgres_password: '',
    postgres_admin_user: 'postgres',
    app_repo: 'https://github.com/frappe/frappe',
    app_branch: 'develop',
    volume_enabled: false,
    volume_pool: 'bench-pool',
    volume_backing: 'image',
    volume_device: '',
    volume_image_size: '60G',
    volume_reservation: '15G',
    volume_quota: '60G',
  }
}

export function useSetup() {
  const { awaitingTerminal } = useSetupHandoff()

  const form = ref(defaultForm())
  const currentStep = ref('loading')
  const errorMessage = ref('')
  const isSubmitting = ref(false)
  const benchName = ref('')
  const isLinux = ref(true)
  const isAlpine = ref(false)
  const dedicatedMariadbWillInstall = ref(false)
  const sharedMariadbWillInstall = ref(false)
  const postgresWillInstall = ref(false)
  const availableBranches = ref([])

  const terminal = ref(null)
  const streamUrl = ref('')
  const streamStatus = ref('Starting…')
  const showStreamDetails = ref(false)

  const volume = useVolumeStorage(form)

  const mariadbWillInstall = computed(() =>
    isLinux.value && form.value.dedicated_db === 'dedicated'
      ? dedicatedMariadbWillInstall.value
      : sharedMariadbWillInstall.value,
  )
  const mariadbPasswordDescription = computed(() =>
    mariadbWillInstall.value
      ? 'MariaDB will be installed and its root password set to this value.'
      : undefined,
  )
  const postgresPasswordDescription = computed(() =>
    postgresWillInstall.value
      ? 'PostgreSQL will be installed and its superuser password set to this value.'
      : undefined,
  )
  const isPostgresDedicated = computed(
    () => isLinux.value && !isAlpine.value && form.value.dedicated_db === 'dedicated',
  )

  const branchOptions = computed(() => {
    const selected = form.value.app_branch
    const isKnown = availableBranches.value.includes(selected)
    const options = availableBranches.value.map((branch) => ({ label: branch, value: branch }))
    return selected && !isKnown ? [{ label: selected, value: selected }, ...options] : options
  })

  const stepSequence = computed(() => {
    const steps = ['passwords', 'database', 'customize']
    const usesVolumes =
      isLinux.value &&
      form.value.db_type === 'mariadb' &&
      form.value.dedicated_db === 'dedicated' &&
      form.value.volume_enabled
    if (usesVolumes) steps.push('storage')
    return steps
  })
  const stepNumber = computed(() => stepSequence.value.indexOf(currentStep.value) + 1)
  const isConfiguring = computed(() => stepNumber.value > 0)
  const isInstalling = computed(() => currentStep.value === 'running')
  const isLastConfigStep = computed(() => currentStep.value === stepSequence.value.at(-1))
  const modalWidthClass = computed(() =>
    isInstalling.value && showStreamDetails.value ? 'max-w-2xl' : 'max-w-lg',
  )
  const stepTitle = computed(() => STEP_TITLES[currentStep.value] || benchName.value)
  const stepSubtitle = computed(() => STEP_SUBTITLES[currentStep.value] || null)

  watch(
    () => form.value.dedicated_db,
    (mode) => {
      if (mode === 'shared') form.value.volume_enabled = false
      if (mode === 'dedicated') form.value.mariadb_admin_user = 'root'
    },
  )
  watch(
    mariadbWillInstall,
    (willInstall) => {
      if (willInstall) form.value.mariadb_admin_user = 'root'
    },
    { immediate: true },
  )

  async function loadConfig() {
    try {
      const config = await setupApi.config()
      benchName.value = config.bench_name || ''
      isLinux.value = config.is_linux !== false
      isAlpine.value = config.is_alpine === true
      volume.availableDevices.value = config.available_devices || []
      volume.rootfsFreeBytes.value = config.rootfs_free_bytes || 0
      for (const key of Object.keys(form.value)) {
        if (config[key] !== undefined) form.value[key] = config[key]
      }
      volume.clampImageSize()
      if (isLinux.value) {
        const instance =
          config.db_type === 'postgres' ? config.postgres_instance : config.mariadb_instance
        form.value.dedicated_db = instance ? 'dedicated' : 'shared'
      }
      if (config.running_setup_task_id) startStream(config.running_setup_task_id)
      else currentStep.value = 'passwords'
    } catch {
      if (currentStep.value === 'loading') currentStep.value = 'passwords'
    }
    loadBranches()
    detectMariadbInstallState()
  }

  async function loadBranches() {
    try {
      availableBranches.value = (await setupApi.branches()).branches || []
    } catch {
      availableBranches.value = []
    }
  }

  async function detectMariadbInstallState() {
    try {
      const [dedicated, shared] = await Promise.all([
        setupApi.validateMariadb({ mariadb_password: '', dedicated_db: true }),
        setupApi.validateMariadb({ mariadb_password: '', dedicated_db: false }),
      ])
      dedicatedMariadbWillInstall.value = dedicated.state === 'will_install'
      sharedMariadbWillInstall.value = shared.state === 'will_install'
    } catch {}
  }

  function startStream(taskId) {
    streamStatus.value = 'Starting…'
    streamUrl.value = setupApi.streamUrl(taskId)
    currentStep.value = 'running'
  }

  function updateStreamStatus(line) {
    const match = line.match(/^\[\d+\/\d+\]\s*(.+?)\.*\s*$/)
    if (match) streamStatus.value = match[1]
  }

  function onStreamDone(success) {
    if (!success) {
      failInstall('Setup failed. Open the details to see what went wrong, then try again.')
      return
    }
    currentStep.value = 'done'
    awaitingTerminal.value = true
    shutdownWizardAndReload()
  }

  function failInstall(message) {
    errorMessage.value = message
    showStreamDetails.value = true
  }

  function toggleStreamDetails() {
    showStreamDetails.value = !showStreamDetails.value
    if (showStreamDetails.value) terminal.value?.scrollToBottom()
  }

  async function validatePasswordStep() {
    if (!form.value.admin_password) return 'Admin password is required'
    return null
  }

  async function validateMariadbStep() {
    if (!form.value.mariadb_password) return 'MariaDB password is required'
    const dedicated = isLinux.value && form.value.dedicated_db === 'dedicated'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validateMariadb({
        mariadb_password: form.value.mariadb_password,
        mariadb_admin_user: form.value.mariadb_admin_user,
        dedicated_db: dedicated,
      })
      if (dedicated) dedicatedMariadbWillInstall.value = state === 'will_install'
      else sharedMariadbWillInstall.value = state === 'will_install'
      if (state === 'invalid') return 'Incorrect MariaDB credentials.'
    } catch {
    } finally {
      isSubmitting.value = false
    }
    return null
  }

  async function validatePostgresStep() {
    if (!form.value.postgres_password) return 'PostgreSQL password is required'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validatePostgres({
        postgres_password: form.value.postgres_password,
        postgres_admin_user: form.value.postgres_admin_user,
        dedicated: isPostgresDedicated.value,
      })
      postgresWillInstall.value = state === 'will_install'
      if (state === 'invalid') return 'Incorrect PostgreSQL credentials.'
    } catch {
    } finally {
      isSubmitting.value = false
    }
    return null
  }

  function validateStorageStep() {
    if (!isLinux.value || !form.value.volume_enabled) return null
    if (form.value.volume_backing === 'device' && !form.value.volume_device)
      return 'Please choose an attached disk.'
    return null
  }

  async function validateDatabaseStep() {
    return form.value.db_type === 'postgres'
      ? validatePostgresStep()
      : validateMariadbStep()
  }

  async function goToNextStep() {
    const validators = {
      passwords: validatePasswordStep,
      database: validateDatabaseStep,
    }
    const message = await validators[currentStep.value]?.()
    if (message) {
      errorMessage.value = message
      return
    }
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) + 1]
  }

  function goToPreviousStep() {
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) - 1]
  }

  function backToConfiguration() {
    errorMessage.value = ''
    showStreamDetails.value = false
    currentStep.value = stepSequence.value.at(-1)
  }

  function buildDatabasePayload() {
    const { db_type, dedicated_db } = form.value
    if (db_type === 'postgres') {
      return {
        mariadb_instance: '',
        mariadb_socket_path: '',
        mariadb_data_dir: '',
        volume_enabled: false,
        postgres_instance: isPostgresDedicated.value ? benchName.value : '',
      }
    }
    if (!isLinux.value) return {}
    if (dedicated_db === 'dedicated') {
      return {
        postgres_instance: '',
        mariadb_instance: benchName.value,
        mariadb_socket_path: `/run/mysqld/mysqld-${benchName.value}.sock`,
        mariadb_data_dir: `/var/lib/mysql-${benchName.value}`,
        mariadb_admin_user: 'root',
      }
    }
    return {
      postgres_instance: '',
      mariadb_instance: '',
      mariadb_socket_path: '',
      mariadb_data_dir: '',
      volume_enabled: false,
    }
  }

  async function saveConfig() {
    const { dedicated_db, ...rest } = form.value
    const result = await setupApi.save({ ...rest, ...buildDatabasePayload() })
    if (!result.ok) throw new Error(result.error || 'Failed to save configuration.')
  }

  async function startSetup() {
    errorMessage.value = validateStorageStep() || ''
    if (errorMessage.value) return
    isSubmitting.value = true
    try {
      await saveConfig()
      const result = await setupApi.start()
      if (!result.ok) throw new Error(result.error || 'Failed to start setup.')
      startStream(result.task_id)
    } catch (error) {
      errorMessage.value = error.message
    } finally {
      isSubmitting.value = false
    }
  }

  async function shutdownWizardAndReload() {
    try {
      await setupApi.finish()
    } catch {}
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      try {
        const response = await setupApi.status()
        if (!response.ok) continue
        const status = await response.json()
        if (status.wizard !== true) return window.location.reload()
      } catch {}
    }
  }

  onMounted(loadConfig)

  return {
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
    deviceOptions: volume.deviceOptions,
    showDeviceDropdown: volume.showDeviceDropdown,
    freeGiB: volume.freeGiB,
    imageSizeGiB: volume.imageSizeGiB,
    imageSizeMinGiB: volume.imageSizeMinGiB,
    imageSizeMaxGiB: volume.imageSizeMaxGiB,
    imageSliderModel: volume.imageSliderModel,
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
  }
}
