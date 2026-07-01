import { ref, onMounted, onBeforeUnmount } from 'vue'
import { processLine } from '../utils/ansi.js'

const MAX_RETRIES = 5

export function useTaskStream({ guardHiddenTab = false } = {}) {
  const terminal = ref(null)
  const lines = ref([])
  const rawLines = ref([])
  const streaming = ref(false)
  let source = null
  let retryTimer = null

  function scrollToBottom() {
    if (guardHiddenTab && document.hidden) return
    terminal.value?.scrollToBottom()
  }

  function push(raw, { overwrite } = {}) {
    if (overwrite) {
      rawLines.value[rawLines.value.length - 1] = raw
      lines.value[lines.value.length - 1] = processLine(raw)
    } else {
      rawLines.value.push(raw)
      lines.value.push(processLine(raw))
    }
    scrollToBottom()
  }

  function close() {
    if (source) {
      source.close()
      source = null
    }
  }

  function start(url, { onDone, onLine, onError } = {}) {
    close()
    streaming.value = true
    let volatile = false
    let retries = 0

    function open() {
      source = new EventSource(url)

      source.onmessage = (event) => {
        retries = 0
        if (volatile) {
          rawLines.value.pop()
          lines.value.pop()
          volatile = false
        }
        push(event.data)
        onLine?.(event.data)
      }

      source.addEventListener('overwrite', (event) => {
        retries = 0
        push(event.data, { overwrite: volatile })
        volatile = true
      })

      source.addEventListener('done', (event) => {
        streaming.value = false
        close()
        onDone?.(parseInt(event.data) === 0)
      })

      source.onerror = () => {
        close()
        if (retries < MAX_RETRIES) {
          retries += 1
          retryTimer = setTimeout(() => {
            // avoid duplicating lines the backend will replay
            rawLines.value = []
            lines.value = []
            open()
          }, 1000 * retries)
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
    close()
    streaming.value = false
  }

  if (guardHiddenTab) {
    onMounted(() => document.addEventListener('visibilitychange', scrollToBottom))
    onBeforeUnmount(() => document.removeEventListener('visibilitychange', scrollToBottom))
  }
  onBeforeUnmount(stop)

  return { terminal, lines, rawLines, streaming, start, stop, scrollToBottom }
}
