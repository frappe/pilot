import { formatBytes } from '@/utils/format'

const stateConfig = {
  running: { label: 'Running', theme: 'green', live: true },
  starting: { label: 'Starting', theme: 'amber', live: true },
  restarting: { label: 'Restarting', theme: 'amber', live: true },
  stopping: { label: 'Stopping', theme: 'amber', live: true },
  stopped: { label: 'Stopped', theme: 'gray' },
  start_failed: { label: 'Failed to start', theme: 'red' },
  failed: { label: 'Failed', theme: 'red' },
  unknown: { label: 'Unknown', theme: 'gray' },
}

export const processState = (state) => stateConfig[state] || stateConfig.unknown

export const isProcessBusy = (state) => state !== 'running' && Boolean(processState(state).live)

export const formatCpu = (percent) => (percent == null ? '—' : `${percent.toFixed(1)}%`)

export const formatMemory = (megabytes) =>
  megabytes == null ? '—' : formatBytes(megabytes * 1024 ** 2)

// Rows sit under their app's heading, so the `mail-` in `mail-stalwart` is noise.
export const processLabel = (name, source) =>
  source === 'bench' || !name.startsWith(`${source}-`) ? name : name.slice(source.length + 1)
