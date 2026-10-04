import { computed, type Ref } from 'vue'

import { fmtDuration } from '@/utils/taskFormat'

// [\w-]+, not \w+: step keys may contain hyphens (e.g. "clear-cache"), and
// \w+ alone would silently fail to match, which reads as "no step" rather
// than a parse error - the STEP_MARKER_RE filter would still hide the raw
// line, so the fold would just vanish with no visible symptom.
const STEP_RE = /^STEP\s([\w-]+),([\d.]+)\s*(.*)/
const STEP_FAILED_RE = /^STEP-FAILED\s([\w-]+),([\d.]+)/

export const STEP_MARKER_RE = /^STEP(-FAILED)?\s/

interface StepMarker {
  key: string
  ts: number
  label: string
  idx: number
}

export interface StepSection {
  key: string
  label: string
  startedAt: number
  endedAt: number | null
  lineStart: number
  lineEnd: number
  status: 'done' | 'failed' | 'running'
}

/**
 * Parses "STEP KEY,TIMESTAMP label" and "STEP-FAILED KEY,TIMESTAMP" markers
 * out of a raw line stream into structured sections with status, timing, and
 * line-range metadata. The backend (TaskReader) already strips the on-disk
 * syslog envelope before these lines reach the UI, so they're plain text here.
 */
export const useTaskSteps = (
  rawLines: Ref<string[]>,
  streaming: Ref<boolean>,
  task: Ref<{ status: string } | null>,
) => {
  const stepSections = computed(() => {
    const markers: StepMarker[] = []
    const failedKeys = new Set<string>()
    rawLines.value.forEach((line, idx) => {
      const m = line.match(STEP_RE)
      if (m) {
        markers.push({ key: m[1], ts: parseFloat(m[2]) * 1000, label: m[3].trim(), idx })
        return
      }
      const f = line.match(STEP_FAILED_RE)
      if (f) failedKeys.add(f[1])
    })

    const sections: StepSection[] = []
    for (let i = 0; i < markers.length; i++) {
      const m = markers[i]
      if (m.key === 'done') break

      const next = markers[i + 1]
      let status: StepSection['status']
      if (failedKeys.has(m.key)) status = 'failed'
      else if (next) status = 'done'
      else if (!streaming.value && task.value?.status === 'failed' && failedKeys.size === 0)
        status = 'failed'
      else if (!streaming.value) status = 'done'
      else status = 'running'

      sections.push({
        key: m.key,
        label: m.label,
        startedAt: m.ts,
        endedAt: next ? next.ts : null,
        lineStart: m.idx + 1,
        lineEnd: next ? next.idx : rawLines.value.length,
        status,
      })
    }
    return sections
  })

  const hasSteps = computed(() => stepSections.value.length > 0)

  const progressPct = computed(() => {
    if (!hasSteps.value) return null
    const done = stepSections.value.filter((s) => s.status === 'done').length
    return Math.round((done / stepSections.value.length) * 100)
  })

  const stepDuration = (section: StepSection) => {
    if (!section.startedAt || !section.endedAt) return null
    return fmtDuration((section.endedAt - section.startedAt) / 1000, { precise: true })
  }

  return { stepSections, hasSteps, progressPct, stepDuration }
}
