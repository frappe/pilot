/** Parse a `text/event-stream` body and call `onData` with each event's JSON payload. */
export const readEventStream = async (
  body: ReadableStream<Uint8Array<ArrayBuffer>>,
  onData: (payload: unknown) => void,
): Promise<void> => {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value
    const events = buffer.split('\n\n')
    buffer = events.pop() ?? ''
    for (const event of events) {
      const data = event
        .split('\n')
        .filter((line) => line.startsWith('data: '))
        .map((line) => line.slice(6))
        .join('\n')
      if (data) onData(JSON.parse(data))
    }
  }
}
