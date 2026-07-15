import { ref, computed, watch, onMounted } from 'vue'
import { useSetupHandoff } from './useSetupHandoff'
import { setupApi } from '../api/setup'
import { meetsPasswordRequirements } from '../utils/passwordStrength'
import { generateRandomPassword } from '../utils/randomPassword'

// Static dropdown options
const DB_TYPE_OPTIONS = [
  { label: 'MariaDB', value: 'mariadb' },
  { label: 'PostgreSQL', value: 'postgres' },
]
const STEP_TITLES = {
  passwords: 'Admin password',
  database: 'Database',
  customize: 'Customize your bench',
  running: 'Setting up your bench',
  done: 'Setup complete',
}
const STEP_SUBTITLES = {
  database: 'Choose and configure your database',
}

export function useSetup() {
  const { awaitingTerminal } = useSetupHandoff()

  // Wizard state
  const currentStep = ref('loading')
  const errorMessage = ref('')
  const isSubmitting = ref(false)
  const benchName = ref('')
  const isLinux = ref(true)
  const isProductionHandoff = ref(false)
  const mariadbWillInstall = ref(false)
  const postgresWillInstall = ref(false)
  const availableBranches = ref([])

  const terminal = ref(null)
  const streamUrl = ref('')
  const streamStatus = ref('Starting…')
  const showStreamDetails = ref(false)

  // User inputs
  const adminPassword = ref('')
  const dbType = ref('mariadb')
  const dbUser = ref('')
  const dbPassword = ref('')
  const appRepo = ref('https://github.com/frappe/frappe')
  const appBranch = ref('develop')

  // Off by default: pilot spawns and owns its own MariaDB/PostgreSQL server.
  const _useExternalDb = ref(false)
  const dbHost = ref('')
  const dbPort = ref('')
  // Clears the password on toggle; loadConfig bypasses this via _useExternalDb.
  const useExternalDb = computed({
    get: () => _useExternalDb.value,
    set: (value) => {
      _useExternalDb.value = value
      dbPassword.value = ''
      if (value) {
        dbHost.value = dbHost.value || '127.0.0.1'
        dbPort.value = dbPort.value || dbPortPlaceholder.value
      } else {
        dbHost.value = ''
        dbPort.value = ''
      }
    },
  })

  // Derived database state. Every bench for this OS user shares one
  // MariaDB/PostgreSQL server (see MariaDBManager/PostgresManager) — there's
  // no per-bench deployment mode to choose, just whether that shared server
  // still needs to be installed and secured, or already exists.
  const dbWillInstall = computed(
    () => !useExternalDb.value && (dbType.value === 'postgres' ? postgresWillInstall.value : mariadbWillInstall.value),
  )
  const isAdminPasswordValid = computed(() => meetsPasswordRequirements(adminPassword.value))

  const showRootUsername = computed(() => useExternalDb.value || !dbWillInstall.value)
  const rootUserPlaceholder = computed(() => (dbType.value === 'mariadb' ? 'root' : 'postgres'))
  const dbPortPlaceholder = computed(() => (dbType.value === 'mariadb' ? '3306' : '5432'))
  // The username the API receives: what the user typed, or the engine default
  // whenever the field is hidden (a fresh install always uses the default).
  const resolvedDbUser = computed(() =>
    showRootUsername.value && dbUser.value ? dbUser.value : rootUserPlaceholder.value,
  )
  const rootPasswordDescription = computed(() => {
    const engine = dbType.value === 'mariadb' ? 'MariaDB' : 'PostgreSQL'
    if (useExternalDb.value) return `Credentials for the existing ${engine} server at ${dbHost.value || 'the given host'}.`
    if (dbWillInstall.value)
      return `${engine} will be installed and its ${dbType.value === 'mariadb' ? 'root' : 'superuser'} password set to this value.`
    return `Using the ${engine} server pilot already manages for this user — enter its existing password.`
  })

  const branchOptions = computed(() => {
    const selected = appBranch.value
    const isKnown = availableBranches.value.includes(selected)
    const options = availableBranches.value.map((branch) => ({ label: branch, value: branch }))
    return selected && !isKnown ? [{ label: selected, value: selected }, ...options] : options
  })

  // Steps
  const stepSequence = computed(() => ['passwords', 'database', 'customize'])
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

  // A fresh install gets a generated password; an existing server keeps its own.
  watch(
    dbWillInstall,
    (willInstall) => {
      if (!willInstall) dbPassword.value = ''
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
      // Bench arrived with production already chosen (the admin UI's "New Bench"
      // flow) — the wizard's task will bring up production itself, so the 'done'
      // step shouldn't tell the user to run `bench setup production` by hand.
      // The flattened config renders an unset manager as the literal string
      // "none" (see BenchTomlBuilder._flatten), not an empty value.
      const processManager = config.production_process_manager
      isProductionHandoff.value = Boolean(processManager) && processManager !== 'none'

      if (config.admin_password) adminPassword.value = config.admin_password
      if (config.db_type) dbType.value = config.db_type
      if (config.app_repo) appRepo.value = config.app_repo
      if (config.app_branch) appBranch.value = config.app_branch
      if (config.db_type === 'postgres') {
        if (config.postgres_admin_user) dbUser.value = config.postgres_admin_user
        if (config.postgres_password) dbPassword.value = config.postgres_password
        if (config.postgres_external) {
          _useExternalDb.value = true
          dbHost.value = config.postgres_host || '127.0.0.1'
          dbPort.value = config.postgres_port ? String(config.postgres_port) : '5432'
        }
      } else {
        if (config.mariadb_admin_user) dbUser.value = config.mariadb_admin_user
        if (config.mariadb_password) dbPassword.value = config.mariadb_password
        if (config.mariadb_external) {
          _useExternalDb.value = true
          dbHost.value = config.mariadb_host || '127.0.0.1'
          dbPort.value = config.mariadb_port ? String(config.mariadb_port) : '3306'
        }
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
      const { state } = await setupApi.validateMariadb({ mariadb_password: '' })
      mariadbWillInstall.value = state === 'will_install'
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
    if (useExternalDb.value && !dbHost.value) return 'Host is required for an external database'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validateMariadb({
        mariadb_password: dbPassword.value,
        mariadb_admin_user: resolvedDbUser.value,
        mariadb_external: useExternalDb.value,
        mariadb_host: useExternalDb.value ? dbHost.value : '',
        mariadb_port: useExternalDb.value ? Number(dbPort.value) || 3306 : undefined,
      })
      mariadbWillInstall.value = state === 'will_install'
      if (state === 'invalid') return 'Incorrect MariaDB credentials.'
    } catch {
    } finally {
      isSubmitting.value = false
    }
    return null
  }

  async function validatePostgresStep() {
    if (!dbPassword.value) return 'PostgreSQL password is required'
    if (useExternalDb.value && !dbHost.value) return 'Host is required for an external database'
    isSubmitting.value = true
    try {
      const { state } = await setupApi.validatePostgres({
        postgres_password: dbPassword.value,
        postgres_admin_user: resolvedDbUser.value,
        postgres_external: useExternalDb.value,
        postgres_host: useExternalDb.value ? dbHost.value : '',
        postgres_port: useExternalDb.value ? Number(dbPort.value) || 5432 : undefined,
      })
      postgresWillInstall.value = state === 'will_install'
      if (state === 'invalid') return 'Incorrect PostgreSQL credentials.'
    } catch {
    } finally {
      isSubmitting.value = false
    }
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

  // Port is only sent in external mode, so a locally customized port isn't clobbered on save.
  function buildPayload() {
    const base = {
      admin_password: adminPassword.value,
      db_type: dbType.value,
      app_repo: appRepo.value,
      app_branch: appBranch.value,
    }
    const external = useExternalDb.value
    // 'localhost', not '', when off — an empty host breaks check_credentials'
    // TCP fallback on systems where the local socket isn't detected.
    const host = external ? dbHost.value : 'localhost'
    const port = external ? Number(dbPort.value) || undefined : undefined
    if (dbType.value === 'postgres') {
      return {
        ...base,
        postgres_password: dbPassword.value,
        postgres_admin_user: resolvedDbUser.value,
        postgres_external: external,
        postgres_host: host,
        ...(port ? { postgres_port: port } : {}),
        mariadb_password: '',
        mariadb_admin_user: 'root',
      }
    }
    return {
      ...base,
      mariadb_password: dbPassword.value,
      mariadb_admin_user: resolvedDbUser.value,
      mariadb_external: external,
      mariadb_host: host,
      ...(port ? { mariadb_port: port } : {}),
      postgres_password: '',
      postgres_admin_user: 'postgres',
    }
  }

  async function saveConfig() {
    const result = await setupApi.save(buildPayload())
    if (!result.ok) throw new Error(result.error || 'Failed to save configuration.')
  }

  async function startSetup() {
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
    dbTypeOptions: DB_TYPE_OPTIONS,
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
  }
}
