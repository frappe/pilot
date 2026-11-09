export type OutputTone = 'muted' | 'error' | 'path' | 'number' | 'name'
export type OutputPart = { text: string; tone?: OutputTone }

const FRAME = /^(\s*File ")(.+)(", line )(\d+)(?:(, in )(.+))?$/
const EXCEPTION = /^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt)(?::.*)?$/
const MUTED =
  /^(?:Traceback \(most recent call last\):|\s*[~^]+\s*|During handling of the above exception, another exception occurred:|The above exception was the direct cause of the following exception:)$/

/** Script output split into lines, with the parts of a Python traceback marked for color. */
export const getOutputLines = (output: string): OutputPart[][] =>
  output.split('\n').map((line): OutputPart[] => {
    const frame = line.match(FRAME)
    if (frame) {
      const [, start, path, middle, number, inText = '', name = ''] = frame
      return [
        { text: start, tone: 'muted' },
        { text: path, tone: 'path' },
        { text: middle, tone: 'muted' },
        { text: number, tone: 'number' },
        { text: inText, tone: 'muted' },
        { text: name, tone: 'name' },
      ]
    }
    if (EXCEPTION.test(line)) return [{ text: line, tone: 'error' }]
    if (MUTED.test(line)) return [{ text: line, tone: 'muted' }]
    return [{ text: line }]
  })
