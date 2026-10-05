import { ref } from 'vue'
import { sitesApi } from '@/api/sites'

// How long a dropped connection is retried before the upload fails.
const RETRY_WINDOW_MS = 10 * 60 * 1000
const MAX_RETRY_DELAY_MS = 30 * 1000

/** A lost connection, a 5xx or a 409 "resume from byte N" is worth retrying. */
const isRetryable = (error: unknown) => {
  const status = (error as { status?: number }).status
  return status === undefined || status === 0 || status === 409 || status >= 500
}

const pause = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new DOMException('The upload was cancelled.', 'AbortError'))
      },
      { once: true },
    )
  })

const identity = (files: Record<string, File>) =>
  JSON.stringify(
    Object.entries(files).map(([part, file]) => [part, file.name, file.size, file.lastModified]),
  )

/** Send backup files in chunks. A failed chunk resumes from the bytes the server has. */
export const useBackupUpload = (siteName: () => string) => {
  const progress = ref<Record<string, number>>({})
  const isUploading = ref(false)
  let uploadId = ''
  let uploadedFiles = ''
  let chunkSize = 0
  let controller: AbortController | null = null

  const received = async (part: string) =>
    (await sitesApi.uploads.status(siteName(), uploadId)).files[part].received

  const sendFile = async (part: string, file: File, signal: AbortSignal) => {
    let offset = await received(part)
    let failedSince = 0
    let delay = 1000
    while (offset < file.size) {
      const start = offset
      try {
        offset = await sitesApi.uploads.sendChunk(
          siteName(),
          uploadId,
          part,
          start,
          file.slice(start, start + chunkSize),
          (sent) => (progress.value[part] = (start + sent) / file.size),
          signal,
        )
        failedSince = 0
        delay = 1000
      } catch (error) {
        if (signal.aborted || !isRetryable(error)) throw error
        failedSince ||= Date.now()
        if (Date.now() - failedSince > RETRY_WINDOW_MS) throw error
        await pause(delay, signal)
        delay = Math.min(delay * 2, MAX_RETRY_DELAY_MS)
        offset = await received(part).catch(() => start)
      }
    }
    progress.value[part] = 1
  }

  /** Upload `files` by part and return the id a restore claims. The same files resume a failed upload. */
  const upload = async (files: Record<string, File>): Promise<string> => {
    controller = new AbortController()
    isUploading.value = true
    try {
      // A restore that claimed the upload, or cleanup of an idle one, removes it on the server.
      const isResumable =
        uploadId &&
        uploadedFiles === identity(files) &&
        (await sitesApi.uploads.exists(siteName(), uploadId))
      if (!isResumable) {
        discard()
        const described = Object.fromEntries(
          Object.entries(files).map(([part, file]) => [part, { filename: file.name, size: file.size }]),
        )
        const started = await sitesApi.uploads.start(siteName(), described)
        uploadId = started.upload_id
        uploadedFiles = identity(files)
        chunkSize = started.chunk_size
      }
      // Closed while starting, before cancel knew the id.
      if (controller.signal.aborted) {
        discard()
        throw new DOMException('The upload was cancelled.', 'AbortError')
      }
      for (const [part, file] of Object.entries(files))
        await sendFile(part, file, controller.signal)
    } finally {
      isUploading.value = false
    }
    return uploadId
  }

  /** Remove the upload from the server; a claimed one belongs to its restore. */
  const discard = () => {
    if (uploadId) sitesApi.uploads.cancel(siteName(), uploadId).catch(() => {})
    uploadId = ''
    uploadedFiles = ''
    progress.value = {}
  }

  /** Stop sending and remove the parts already on the server. */
  const cancel = () => {
    controller?.abort()
    discard()
  }

  return { progress, isUploading, upload, cancel }
}
