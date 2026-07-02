// Excludes quote/shell-special characters and visually ambiguous ones (0/O, 1/l/I)
// since this ends up in bench.toml, SQL statements, and shell commands.
const CHARSET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'

export function generateRandomPassword(length = 32) {
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  return Array.from(bytes, (byte) => CHARSET[byte % CHARSET.length]).join('')
}
