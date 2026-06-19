import { ref, onMounted, onBeforeUnmount } from 'vue'
import { processLine } from '../utils/ansi.js'

export function useTaskStream() {
  const terminal = ref(null)
  const lines = ref([])     // processed (ANSI → HTML-safe spans)
  const rawLines = ref([])  // raw text (for callers that need to parse markers)
  const streaming = ref(false)
  let es = null

  function scrollToBottom() {
    if (!document.hidden) terminal.value?.scrollToBottom()
  }

  // start() does NOT clear lines — caller clears if needed (Setup resets on each
  // run; TaskDetail appends to lines already loaded by its REST fetch).
  function start(url, { onDone, onLine, onError } = {}) {
    if (es) { es.close(); es = null }
    streaming.value = true
    let volatile = false

    es = new EventSource(url)
    es.onmessage = (e) => {
      const raw = e.data
      if (volatile) { rawLines.value.pop(); lines.value.pop(); volatile = false }
      rawLines.value.push(raw)
      lines.value.push(processLine(raw))
      onLine?.(raw)
      scrollToBottom()
    }
    es.addEventListener('overwrite', (e) => {
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
      streaming.value = false
      if (es) { es.close(); es = null }
      onError?.()
    }
  }

  function stop() {
    if (es) { es.close(); es = null }
    streaming.value = false
  }

  onMounted(() => document.addEventListener('visibilitychange', scrollToBottom))
  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', scrollToBottom)
    stop()
  })

  return { terminal, lines, rawLines, streaming, start, stop, scrollToBottom }
}
