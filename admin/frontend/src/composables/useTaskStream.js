import { ref, onMounted, onBeforeUnmount } from 'vue'
import { processLine } from '../utils/ansi.js'

// guardHiddenTab: skip scrolling while the browser tab is hidden and re-scroll
// on visibility change. Needed when the TerminalOutput sits behind a v-if (Setup)
// — calling scrollTop against an unpainted layout causes a blank-area jump.
// Leave it off when the terminal is always mounted (TaskDetail).
export function useTaskStream({ guardHiddenTab = false } = {}) {
  const terminal = ref(null)
  const lines = ref([])     // processed (ANSI → HTML-safe spans)
  const rawLines = ref([])  // raw text (for callers that need to parse markers)
  const streaming = ref(false)
  let es = null
  let retryTimer = null

  function scrollToBottom() {
    if (guardHiddenTab && document.hidden) return
    terminal.value?.scrollToBottom()
  }

  // start() does NOT clear lines — caller clears if needed (Setup resets on each
  // run; TaskDetail appends to lines already loaded by its REST fetch).
  const MAX_RETRIES = 5

  function start(url, { onDone, onLine, onError } = {}) {
    if (es) { es.close(); es = null }
    streaming.value = true
    let volatile = false
    let retries = 0

    function open() {
      es = new EventSource(url)
      es.onmessage = (e) => {
        retries = 0
        const raw = e.data
        if (volatile) { rawLines.value.pop(); lines.value.pop(); volatile = false }
        rawLines.value.push(raw)
        lines.value.push(processLine(raw))
        onLine?.(raw)
        scrollToBottom()
      }
      es.addEventListener('overwrite', (e) => {
        retries = 0
        const raw = e.data
        if (volatile) {
          rawLines.value[rawLines.value.length - 1] = raw
          lines.value[lines.value.length - 1] = processLine(raw)
        } else {
          rawLines.value.push(raw)
          lines.value.push(processLine(raw))
          volatile = true
        }
        scrollToBottom()
      })
      es.addEventListener('done', (e) => {
        streaming.value = false
        es.close(); es = null
        onDone?.(parseInt(e.data) === 0)
      })
      es.onerror = () => {
        if (es) { es.close(); es = null }
        if (retries < MAX_RETRIES) {
          retries++
          retryTimer = setTimeout(open, 1000 * retries)
        } else {
          streaming.value = false
          onError?.()
        }
      }
    }

    open()
  }

  function stop() {
    clearTimeout(retryTimer)
    retryTimer = null
    if (es) { es.close(); es = null }
    streaming.value = false
  }

  if (guardHiddenTab) {
    onMounted(() => document.addEventListener('visibilitychange', scrollToBottom))
    onBeforeUnmount(() => document.removeEventListener('visibilitychange', scrollToBottom))
  }
  onBeforeUnmount(stop)

  return { terminal, lines, rawLines, streaming, start, stop, scrollToBottom }
}
