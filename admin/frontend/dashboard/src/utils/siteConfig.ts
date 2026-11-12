export const CONFIG_TYPES = ['String', 'Number', 'Boolean', 'JSON', 'Password'] as const

export type ConfigType = (typeof CONFIG_TYPES)[number]

/** What the API returns for a write-only secret, such as encryption_key. */
export const SECRET_MASK = '********'

export type ConfigKey = {
  key: string
  title: string
  type: ConfigType
  description?: string
}

/** The type that fits a value already in the config file. */
export const inferConfigType = (value: unknown): ConfigType => {
  if (typeof value === 'boolean') return 'Boolean'
  if (typeof value === 'number') return 'Number'
  if (value !== null && typeof value === 'object') return 'JSON'
  return 'String'
}

/** The text an input shows for a stored value of this type. */
export const formatConfigValue = (value: unknown, type: ConfigType): string => {
  if (value === undefined || value === null) return ''
  if (type === 'Boolean') return value ? 'true' : 'false'
  if (type === 'JSON') return JSON.stringify(value, null, 2)
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** Cast the input text to the value saved for this type. Throws a message for the user. */
export const castConfigValue = (raw: string, type: ConfigType): unknown => {
  if (type === 'Number') {
    const number = Number(raw)
    if (!raw.trim() || !Number.isFinite(number)) throw new Error('Value must be a number.')
    return number
  }
  if (type === 'Boolean') {
    if (raw !== 'true' && raw !== 'false') throw new Error('Value must be true or false.')
    return raw === 'true'
  }
  if (type === 'Password' && !raw) throw new Error('Enter a value.')
  if (type === 'JSON') {
    try {
      return JSON.parse(raw)
    } catch {
      throw new Error('Value must be valid JSON.')
    }
  }
  return raw
}
