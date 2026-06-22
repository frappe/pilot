import { computed } from 'vue'

const STEP_RE = /^##\[step:(\w+),([\d.]+)\]\s*(.*)/

/**
 * Parses ##[step:KEY,TIMESTAMP] markers out of a raw line stream into
 * structured sections with status, timing, and line-range metadata.
 *
 * @param {import('vue').Ref<string[]>} rawLines
 * @param {import('vue').Ref<boolean>}  streaming
 * @param {import('vue').Ref<object|null>} task
 */
export function useTaskSteps(rawLines, streaming, task) {
  const stepSections = computed(() => {
    const markers = []
    rawLines.value.forEach((line, idx) => {
      const m = line.match(STEP_RE)
      if (m) markers.push({ key: m[1], ts: parseFloat(m[2]) * 1000, label: m[3].trim(), idx })
    })

    const sections = []
    for (let i = 0; i < markers.length; i++) {
      const m = markers[i]
      if (m.key === 'done') break

      const next = markers[i + 1]
      let status
      if (next) status = 'done'
      else if (!streaming.value && task.value?.status === 'failed') status = 'failed'
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
