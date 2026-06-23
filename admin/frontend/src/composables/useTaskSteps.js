import { computed } from 'vue'

const STEP_RE = /^##\[step:(\w+),([\d.]+)\]\s*(.*)/
const STEP_FAILED_RE = /^##\[step-failed:(\w+),([\d.]+)\]/

export const STEP_MARKER_RE = /^##\[step(-failed)?:/

/**
 * Parses ##[step:KEY,TIMESTAMP] and ##[step-failed:KEY,TIMESTAMP] markers out
 * of a raw line stream into structured sections with status, timing, and
 * line-range metadata.
 *
 * @param {import('vue').Ref<string[]>} rawLines
 * @param {import('vue').Ref<boolean>}  streaming
 * @param {import('vue').Ref<object|null>} task
 */
export function useTaskSteps(rawLines, streaming, task) {
  const stepSections = computed(() => {
    const markers = []
    const failedKeys = new Set()
    rawLines.value.forEach((line, idx) => {
      const m = line.match(STEP_RE)
      if (m) { markers.push({ key: m[1], ts: parseFloat(m[2]) * 1000, label: m[3].trim(), idx }); return }
      const f = line.match(STEP_FAILED_RE)
      if (f) failedKeys.add(f[1])
    })

    const sections = []
    for (let i = 0; i < markers.length; i++) {
      const m = markers[i]
      if (m.key === 'done') break

      const next = markers[i + 1]
      let status
      if (failedKeys.has(m.key)) status = 'failed'
      else if (next) status = 'done'
      else if (!streaming.value && task.value?.status === 'failed' && failedKeys.size === 0) status = 'failed'
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
    const done = stepSections.value.filter(s => s.status === 'done').length
    return Math.round((done / stepSections.value.length) * 100)
  })

  function stepDuration(section) {
    if (!section.startedAt || !section.endedAt) return null
    const s = (section.endedAt - section.startedAt) / 1000
    return s < 60 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`
  }

  return { stepSections, hasSteps, progressPct, stepDuration }
}
