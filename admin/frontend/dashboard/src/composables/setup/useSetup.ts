import { ref, computed, watch, onMounted } from 'vue'

import { useSetupHandoff } from '@/composables/setup/useSetupHandoff'
import { apiErrorMessage } from '@/api/client'
import { gitApi } from '@/api/git'
import { setupApi } from '@/api/setup'
import { branchComboboxOptions } from '@/utils/branchComboboxOptions'

// Excludes quote/shell-special characters and visually ambiguous ones (0/O, 1/l/I)
// since this ends up in bench.toml, SQL statements, and shell commands.
const charset = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'

const generateRandomPassword = (length = 32) => {
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  return Array.from(bytes, (byte) => charset[byte % charset.length]).join('')
}

// Static dropdown options
const DB_TYPE_OPTIONS = [
  { label: 'MariaDB', value: 'mariadb' },
  { label: 'PostgreSQL', value: 'postgres' },
]
const STEP_TITLES = {
  database: 'Database',
  customize: 'Customize your bench',
  running: 'Setting up your bench',
  done: 'Setup complete',
}
const STEP_SUBTITLES = {
  database: 'Choose and configure your database',
}

export const useSetup = () => {
  const { awaitingTerminal } = useSetupHandoff()

  // Wizard state
  const currentStep = ref('loading')
  const errorMessage = ref('')
  const isSubmitting = ref(false)
  const benchName = ref('')
  const isLinux = ref(true)
  const isProductionHandoff = ref(false)
  const availableBranches = ref([])
  const mariadbPasswordConfigured = ref(false)
  const postgresPasswordConfigured = ref(false)
  const mariadbLocalAvailable = ref(false)
  const postgresLocalAvailable = ref(false)

  const terminal = ref(null)
  const setupTaskId = ref('')
  const streamUrl = ref('')
  const streamStatus = ref('Starting…')
  const showStreamDetails = ref(false)

  // User inputs
  const dbType = ref('mariadb')
  const dbUser = ref('')
  const dbPassword = ref('')
  const appRepo = ref('https://github.com/frappe/frappe')
  const appBranch = ref('develop')
  const validatingFramework = ref(false)
  const resolvedFramework = ref<{ repo: string; branch: string } | null>(null)

  const localAvailable = computed(() =>
    dbType.value === 'postgres' ? postgresLocalAvailable.value : mariadbLocalAvailable.value,
  )
  const dbMode = ref('new')
  const dbModeOptions = computed(() => [
    localAvailable.value
      ? { label: 'Use existing database', value: 'existing_local' }
      : { label: 'Create new database', value: 'new' },
    { label: 'Connect to external database', value: 'external' },
  ])
  watch(
    localAvailable,
    (available) => {
      if (dbMode.value !== 'external') dbMode.value = available ? 'existing_local' : 'new'
    },
    { immediate: true },
  )
  const dbHost = ref('')
  const dbPort = ref('')

  const dbPasswordConfigured = computed(() =>
    dbType.value === 'postgres'
      ? postgresPasswordConfigured.value
      : mariadbPasswordConfigured.value,
  )

  const rootUserPlaceholder = computed(() => (dbType.value === 'mariadb' ? 'root' : 'postgres'))
  const dbPortPlaceholder = computed(() => (dbType.value === 'mariadb' ? '3306' : '5432'))
  const resolvedDbUser = computed(() => dbUser.value || rootUserPlaceholder.value)

  const branchOptions = computed(() =>
    branchComboboxOptions(availableBranches.value, appBranch.value, (typed) => {
      appBranch.value = typed
    }),
  )
  const frameworkIsValid = computed(() => {
    const resolved = resolvedFramework.value
    return Boolean(
      resolved?.repo === appRepo.value.trim() && resolved?.branch === appBranch.value.trim(),
    )
  })

  // Steps
  const stepSequence = computed(() => ['database', 'customize'])
  const stepNumber = computed(() => stepSequence.value.indexOf(currentStep.value) + 1)
  const isConfiguring = computed(() => stepNumber.value > 0)
  const isInstalling = computed(() => currentStep.value === 'running')
  const isLastConfigStep = computed(() => currentStep.value === stepSequence.value.at(-1))
  const modalWidthClass = computed(() =>
    isInstalling.value && showStreamDetails.value ? 'max-w-2xl' : 'max-w-lg',
  )
  const isDone = computed(() => currentStep.value === 'done')
  const pilotCommand = computed(() => (benchName.value ? `pilot -b ${benchName.value}` : 'pilot'))
  const stepTitle = computed(() => {
    if (isDone.value && isProductionHandoff.value) return 'Finishing setup'
    return STEP_TITLES[currentStep.value] || benchName.value
  })
  const stepSubtitle = computed(() => STEP_SUBTITLES[currentStep.value] || null)

  // Loading
  const loadConfig = async () => {
    try {
      const config = await setupApi.config()
      benchName.value = config.bench_name || ''
      isLinux.value = config.is_linux !== false
      mariadbPasswordConfigured.value = config.mariadb_password_configured === true
      postgresPasswordConfigured.value = config.postgres_password_configured === true
      // Bench arrived with production already chosen (the admin UI's "New Bench"
      // flow) - the wizard's task will bring up production itself, so the 'done'
      // step shouldn't tell the user to run `pilot setup production` by hand.
      // The flattened config renders an unset manager as the literal string
      // "none" (see BenchTomlBuilder._flatten), not an empty value.
      const processManager = config.production_process_manager
      isProductionHandoff.value = Boolean(processManager) && processManager !== 'none'

      mariadbLocalAvailable.value = config.mariadb_local_available === true
      postgresLocalAvailable.value = config.postgres_local_available === true

      if (config.db_type) dbType.value = config.db_type
      if (config.app_repo) appRepo.value = config.app_repo
      if (config.app_branch) appBranch.value = config.app_branch
      if (config.db_type === 'postgres') {
        if (config.postgres_admin_user) dbUser.value = config.postgres_admin_user
        if (config.postgres_existing) {
          dbMode.value = 'external'
          dbHost.value = config.postgres_host || '127.0.0.1'
          dbPort.value = config.postgres_port ? String(config.postgres_port) : '5432'
        }
      } else {
        if (config.mariadb_admin_user) dbUser.value = config.mariadb_admin_user
        if (config.mariadb_existing) {
          dbMode.value = 'external'
          dbHost.value = config.mariadb_host || '127.0.0.1'
          dbPort.value = config.mariadb_port ? String(config.mariadb_port) : '3306'
        }
      }

      if (config.running_setup_task_id) startStream(config.running_setup_task_id)
      else currentStep.value = 'database'
    } catch {
      if (currentStep.value === 'loading') currentStep.value = 'database'
    }
    loadBranches()
  }

  const loadBranches = async () => {
    try {
      availableBranches.value = (await setupApi.branches()).branches || []
    } catch {
      availableBranches.value = []
    }
  }

  // Stream
  const startStream = (taskId) => {
    setupTaskId.value = taskId
    streamStatus.value = 'Starting…'
    streamUrl.value = setupApi.streamUrl(taskId)
    currentStep.value = 'running'
  }

  const updateStreamStatus = (line) => {
    const match = line.match(/^\[\d+\/\d+\]\s*(.+?)\.*\s*$/)
    if (match) streamStatus.value = match[1]
  }

  const onStreamDone = (success) => {
    if (!success) {
      failInstall('Setup failed. Open the details to see what went wrong, then try again.')
      return
    }
    currentStep.value = 'done'
    awaitingTerminal.value = true
    shutdownWizardAndReload()
  }

  const failInstall = (message) => {
    errorMessage.value = message
    showStreamDetails.value = true
  }

  const toggleStreamDetails = () => {
    showStreamDetails.value = !showStreamDetails.value
    if (showStreamDetails.value) terminal.value?.scrollToBottom()
  }

  // Validation
  let frameworkRequest = 0

  const validateFramework = async (repo: string, branch: string) => {
    const request = ++frameworkRequest
    validatingFramework.value = true
    resolvedFramework.value = null
    errorMessage.value = ''
    try {
      const result = await gitApi.resolve(repo, branch)
      if (
        request !== frameworkRequest ||
        repo !== appRepo.value.trim() ||
        branch !== appBranch.value.trim()
      )
        return false
      if (!result.name) {
        errorMessage.value = apiErrorMessage(result, 'Could not validate the Frappe branch.')
        return false
      }
      if (result.name !== 'frappe') {
        errorMessage.value = 'The repository does not contain the Frappe app.'
        return false
      }
      resolvedFramework.value = { repo, branch }
      return true
    } catch (error) {
      if (request === frameworkRequest) {
        errorMessage.value = error.message || 'Could not validate the Frappe branch.'
      }
      return false
    } finally {
      if (request === frameworkRequest) validatingFramework.value = false
    }
  }

  watch([currentStep, appRepo, appBranch], ([step, repo, branch]) => {
    frameworkRequest += 1
    validatingFramework.value = false
    resolvedFramework.value = null
    if (step !== 'customize') return
    errorMessage.value = ''
    const selectedRepo = repo.trim()
    const selectedBranch = branch.trim()
    if (selectedRepo && selectedBranch) validateFramework(selectedRepo, selectedBranch)
  })

  const validateDatabaseStep = async () => {
    if (dbMode.value !== 'external') return null
    const databaseName = dbType.value === 'postgres' ? 'PostgreSQL' : 'MariaDB'
    if (!dbHost.value) return 'Host is required for an external database'
    if (!dbPassword.value && !dbPasswordConfigured.value)
      return `${databaseName} password is required`
    if (!dbPassword.value) return null
    isSubmitting.value = true
    try {
      const result = await setupApi.validateDatabase({
        engine: dbType.value,
        password: dbPassword.value,
        admin_user: resolvedDbUser.value,
        existing: true,
        host: dbHost.value,
        port: Number(dbPort.value) || Number(dbPortPlaceholder.value),
      })
      if (result.error) {
        return apiErrorMessage(result, `Could not validate the ${databaseName} configuration.`)
      }
      if (result.state === 'invalid') return `Incorrect ${databaseName} credentials.`
      if (result.state !== 'valid') {
        return `Could not validate the ${databaseName} configuration.`
      }
    } catch (error) {
      return error.message || `Could not validate the ${databaseName} configuration.`
    } finally {
      isSubmitting.value = false
    }
    return null
  }

  // Navigation
  const goToNextStep = async () => {
    const validators = { database: validateDatabaseStep }
    const message = await validators[currentStep.value]?.()
    if (message) {
      errorMessage.value = message
      return
    }
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) + 1]
  }

  const goToPreviousStep = () => {
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) - 1]
  }

  const backToConfiguration = () => {
    errorMessage.value = ''
    showStreamDetails.value = false
    currentStep.value = stepSequence.value.at(-1)
  }

  const buildPayload = () => {
    const base = {
      db_type: dbType.value,
      app_repo: appRepo.value.trim(),
      app_branch: appBranch.value.trim(),
    }
    if (dbMode.value === 'existing_local') {
      return { ...base, db_mode: 'existing_local' }
    }
    if (dbMode.value === 'new') {
      const passwordField = dbType.value === 'postgres' ? 'postgres_password' : 'mariadb_password'
      return {
        ...base,
        ...(dbPasswordConfigured.value ? {} : { [passwordField]: generateRandomPassword() }),
      }
    }
    const port = Number(dbPort.value) || undefined
    if (dbType.value === 'postgres') {
      return {
        ...base,
        ...(dbPassword.value ? { postgres_password: dbPassword.value } : {}),
        postgres_admin_user: resolvedDbUser.value,
        postgres_existing: true,
        postgres_host: dbHost.value,
        ...(port ? { postgres_port: port } : {}),
      }
    }
    return {
      ...base,
      ...(dbPassword.value ? { mariadb_password: dbPassword.value } : {}),
      mariadb_admin_user: resolvedDbUser.value,
      mariadb_existing: true,
      mariadb_host: dbHost.value,
      ...(port ? { mariadb_port: port } : {}),
    }
  }

  const saveConfig = async () => {
    const result = await setupApi.save(buildPayload())
    if (result.error) throw new Error(apiErrorMessage(result, 'Failed to save configuration.'))
  }

  const startSetup = async () => {
    const repo = appRepo.value.trim()
    const branch = appBranch.value.trim()
    isSubmitting.value = true
    errorMessage.value = ''
    try {
      if (!repo) throw new Error('Frappe repository is required.')
      if (!branch) throw new Error('Frappe branch is required.')
      if (!(await validateFramework(repo, branch))) return
      await saveConfig()
      const result = await setupApi.start()
      if (result.error) throw new Error(apiErrorMessage(result, 'Failed to start setup.'))
      if (!result.task_id) throw new Error('Setup did not return a task to follow.')
      startStream(result.task_id)
    } catch (error) {
      errorMessage.value = error.message
    } finally {
      isSubmitting.value = false
    }
  }

  const shutdownWizardAndReload = async () => {
    while (setupTaskId.value) {
      try {
        const response = await setupApi.finish(setupTaskId.value)
        if (response.ok) break
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 3000))
    }
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      try {
        const bootstrap = await setupApi.bootstrap()
        if (bootstrap.mode === 'admin') return (window.location.href = '/sites')
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
    dbTypeOptions: DB_TYPE_OPTIONS,
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
  }
}
