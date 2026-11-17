interface KnownError {
  pattern: RegExp
  message: string
}

const knownErrors: KnownError[] = [
  {
    pattern: /no space left on device|ENOSPC|disk quota exceeded|not enough space/i,
    message: 'The server is out of disk space. Free up space or upgrade the storage, then retry.',
  },
  {
    pattern: /out of memory|cannot allocate memory|ENOMEM/i,
    message: 'The server ran out of memory. Free up memory or upgrade the server, then retry.',
  },
  {
    pattern: /ETIMEDOUT|ECONNRESET|getaddrinfo|temporary failure in name resolution/i,
    message: 'The server could not reach the network. Check its connection, then retry.',
  },
]

export const knownErrorMessage = (...texts: (string | undefined | null)[]): string | null => {
  const text = texts.filter(Boolean).join('\n')

  return knownErrors.find(({ pattern }) => pattern.test(text))?.message ?? null
}
