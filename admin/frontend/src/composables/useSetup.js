import { ref, computed, watch, onMounted } from 'vue'
import { useSetupHandoff } from './useSetupHandoff'
import { useVolumeStorage } from './useVolumeStorage'
import { setupApi } from '../api/setup'
import { meetsPasswordRequirements } from '../utils/passwordStrength'
import { generateRandomPassword } from '../utils/randomPassword'

// Static dropdown options
const DB_TYPE_OPTIONS = [
  { label: 'MariaDB', value: 'mariadb' },
  { label: 'PostgreSQL', value: 'postgres' },
]
const DEPLOYMENT_OPTIONS = [
  { label: 'Dedicated Instance (Recommended)', value: 'dedicated' },
  { label: 'Shared Instance', value: 'shared' },
]
const STORAGE_OPTIONS = [
  { label: "This machine's disk", value: 'image' },
  { label: 'An attached disk', value: 'device' },
]

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

export function useSetup() {
  const { awaitingTerminal } = useSetupHandoff()

  // Wizard state
  const currentStep = ref('loading')
  const errorMessage = ref('')
  const isSubmitting = ref(false)
  const benchName = ref('')
  const isLinux = ref(true)
  const isAlpine = ref(false)
  const isProductionHandoff = ref(false)
  const dedicatedMariadbWillInstall = ref(false)
  const sharedMariadbWillInstall = ref(false)
  const postgresWillInstall = ref(false)
  const availableBranches = ref([])

  const terminal = ref(null)
  const streamUrl = ref('')
  const streamStatus = ref('Starting…')
  const showStreamDetails = ref(false)

  // User inputs
  const adminPassword = ref('')
  const dbType = ref('mariadb')
  const deploymentMode = ref('dedicated')
  const dbUser = ref('')
  const dbPassword = ref('')
  const appRepo = ref('https://github.com/frappe/frappe')
  const appBranch = ref('develop')
  const volumeEnabled = ref(false)
  const volumeBacking = ref('image')
  const volumeDevice = ref('')
  const volumeImageSize = ref('60G')

  const volume = useVolumeStorage(volumeBacking, volumeDevice, volumeImageSize)

  // Derived database state
  const mariadbWillInstall = computed(() =>
    isLinux.value && deploymentMode.value === 'dedicated'
      ? dedicatedMariadbWillInstall.value
      : sharedMariadbWillInstall.value,
  )
  const isAdminPasswordValid = computed(() => meetsPasswordRequirements(adminPassword.value))
  const isPostgresDedicated = computed(
    () => isLinux.value && !isAlpine.value && deploymentMode.value === 'dedicated',
  )

  // Database step: MariaDB and PostgreSQL share one set of fields
  const showDeploymentMode = computed(() => {
    if (dbType.value === 'mariadb') return isLinux.value
    return isLinux.value && !isAlpine.value
  })
  const showRootUsername = computed(() => {
    if (dbType.value === 'mariadb') {
      return (!isLinux.value || deploymentMode.value === 'shared') && !mariadbWillInstall.value
    }
    return !isPostgresDedicated.value
  })
  const rootUserPlaceholder = computed(() => (dbType.value === 'mariadb' ? 'root' : 'postgres'))
  // The username the API receives: what the user typed, or the engine default
  // whenever the field is hidden (dedicated instances and fresh installs).
  const resolvedDbUser = computed(() =>
    showRootUsername.value && dbUser.value ? dbUser.value : rootUserPlaceholder.value,
  )
  const rootPasswordDescription = computed(() => {
    if (dbType.value === 'mariadb') {
      return mariadbWillInstall.value
        ? 'MariaDB will be installed and its root password set to this value.'
        : undefined
    }
    return postgresWillInstall.value
      ? 'PostgreSQL will be installed and its superuser password set to this value.'
      : undefined
  })

  const branchOptions = computed(() => {
    const selected = appBranch.value
    const isKnown = availableBranches.value.includes(selected)
    const options = availableBranches.value.map((branch) => ({ label: branch, value: branch }))
    return selected && !isKnown ? [{ label: selected, value: selected }, ...options] : options
  })

  // Steps
  const stepSequence = computed(() => {
    const steps = ['passwords', 'database', 'customize']
    const usesVolumes =
      isLinux.value &&
      dbType.value === 'mariadb' &&
      deploymentMode.value === 'dedicated' &&
      volumeEnabled.value
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
  const isDone = computed(() => currentStep.value === 'done')
  const stepTitle = computed(() => {
    if (isDone.value && isProductionHandoff.value) return 'Finishing setup'
    return STEP_TITLES[currentStep.value] || benchName.value
  })
  const stepSubtitle = computed(() => STEP_SUBTITLES[currentStep.value] || null)

  // A dedicated instance is ours to provision, so give it a generated password.
  // A shared instance keeps its own, so the field is cleared for re-entry.
  watch(
    deploymentMode,
    (mode) => {
      if (mode === 'shared') dbPassword.value = ''
      else if (!dbPassword.value) dbPassword.value = generateRandomPassword()
    },
    { immediate: true, flush: 'post' },
  )

  // Loading
  async function loadConfig() {
    try {
      const config = await setupApi.config()
      benchName.value = config.bench_name || ''
      isLinux.value = config.is_linux !== false
      isAlpine.value = config.is_alpine === true
      // Bench arrived with production already chosen (the admin UI's "New Bench"
      // flow) — the wizard's task will bring up production itself, so the 'done'
      // step shouldn't tell the user to run `bench setup production` by hand.
      // The flattened config renders an unset manager as the literal string
      // "none" (see BenchTomlBuilder._flatten), not an empty value.
      const processManager = config.production_process_manager
      isProductionHandoff.value = Boolean(processManager) && processManager !== 'none'
      volume.availableDevices.value = config.available_devices || []
      volume.rootfsFreeBytes.value = config.rootfs_free_bytes || 0

      if (config.admin_password) adminPassword.value = config.admin_password
      if (config.db_type) dbType.value = config.db_type
      if (config.app_repo) appRepo.value = config.app_repo
      if (config.app_branch) appBranch.value = config.app_branch
      if (config.volume_enabled !== undefined) volumeEnabled.value = config.volume_enabled
      if (config.volume_backing) volumeBacking.value = config.volume_backing
      if (config.volume_device) volumeDevice.value = config.volume_device
      if (config.volume_image_size) volumeImageSize.value = config.volume_image_size
      if (config.db_type === 'postgres') {
        if (config.postgres_admin_user) dbUser.value = config.postgres_admin_user
        if (config.postgres_password) dbPassword.value = config.postgres_password
      } else {
        if (config.mariadb_admin_user) dbUser.value = config.mariadb_admin_user
        if (config.mariadb_password) dbPassword.value = config.mariadb_password
      }

      volume.clampImageSize()
      if (isLinux.value) {
        const instance =
          config.db_type === 'postgres' ? config.postgres_instance : config.mariadb_instance
        deploymentMode.value = instance ? 'dedicated' : 'shared'
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

  // Stream
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

  // Validation
  function validatePasswordStep() {
    if (!adminPassword.value) return 'Admin password is required'
    if (!meetsPasswordRequirements(adminPassword.value))
      return 'Password does not meet all requirements'
    return null
  }

  async function validateMariadbStep() {
    if (!dbPassword.value) return 'MariaDB password is required'
    const dedicated = isLinux.value && deploymentMode.value === 'dedicated'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validateMariadb({
        mariadb_password: dbPassword.value,
        mariadb_admin_user: resolvedDbUser.value,
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
    if (!dbPassword.value) return 'PostgreSQL password is required'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validatePostgres({
        postgres_password: dbPassword.value,
        postgres_admin_user: resolvedDbUser.value,
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
    if (!isLinux.value || !volumeEnabled.value) return null
    if (volumeBacking.value === 'device' && !volumeDevice.value)
      return 'Please choose an attached disk.'
    return null
  }

  function validateDatabaseStep() {
    return dbType.value === 'postgres' ? validatePostgresStep() : validateMariadbStep()
  }

  // Navigation
  async function goToNextStep() {
    const validators = { passwords: validatePasswordStep, database: validateDatabaseStep }
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

  // Save: the payload is assembled from the current dropdown values
  function buildPayload() {
    const base = {
      admin_password: adminPassword.value,
      db_type: dbType.value,
      app_repo: appRepo.value,
      app_branch: appBranch.value,
      volume_pool: 'bench-pool',
      volume_backing: volumeBacking.value,
      volume_device: volumeDevice.value,
      volume_image_size: volumeImageSize.value,
      ...volume.volumeSizes.value,
    }
    if (dbType.value === 'postgres') {
      return {
        ...base,
        postgres_password: dbPassword.value,
        postgres_admin_user: resolvedDbUser.value,
        postgres_instance: isPostgresDedicated.value ? benchName.value : '',
        mariadb_password: '',
        mariadb_admin_user: 'root',
        mariadb_instance: '',
        mariadb_socket_path: '',
        mariadb_data_dir: '',
        volume_enabled: false,
      }
    }
    const mariadb = {
      ...base,
      mariadb_password: dbPassword.value,
      mariadb_admin_user: resolvedDbUser.value,
      postgres_password: '',
      postgres_admin_user: 'postgres',
    }
    if (!isLinux.value) return { ...mariadb, volume_enabled: false }
    if (deploymentMode.value === 'dedicated') {
      return {
        ...mariadb,
        mariadb_admin_user: 'root',
        mariadb_instance: benchName.value,
        mariadb_socket_path: `/run/mysqld/mysqld-${benchName.value}.sock`,
        mariadb_data_dir: `/var/lib/mysql-${benchName.value}`,
        postgres_instance: '',
        volume_enabled: volumeEnabled.value,
      }
    }
    return {
      ...mariadb,
      mariadb_instance: '',
      mariadb_socket_path: '',
      mariadb_data_dir: '',
      postgres_instance: '',
      volume_enabled: false,
    }
  }

  async function saveConfig() {
    const result = await setupApi.save(buildPayload())
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
        if (status.wizard !== true) return (window.location.href = '/sites')
      } catch {}
    }
  }

  onMounted(loadConfig)

  return {
    currentStep,
    errorMessage,
    isSubmitting,
    isLinux,
    isAlpine,
    isProductionHandoff,
    isDone,
    terminal,
    streamUrl,
    streamStatus,
    showStreamDetails,
    isAdminPasswordValid,
    adminPassword,
    dbType,
    deploymentMode,
    dbUser,
    dbPassword,
    appRepo,
    appBranch,
    volumeEnabled,
    volumeBacking,
    volumeDevice,
    showDeploymentMode,
    showRootUsername,
    rootUserPlaceholder,
    rootPasswordDescription,
    dbTypeOptions: DB_TYPE_OPTIONS,
    deploymentOptions: DEPLOYMENT_OPTIONS,
    storageOptions: STORAGE_OPTIONS,
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
