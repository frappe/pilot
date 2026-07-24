export const PASSWORD_REQUIREMENTS = [
  { label: 'At least 8 characters', test: (pwd) => pwd.length >= 8 },
  { label: 'Upper & lower case letters', test: (pwd) => /[a-z]/.test(pwd) && /[A-Z]/.test(pwd) },
  { label: 'At least one number', test: (pwd) => /\d/.test(pwd) },
  { label: 'At least one symbol', test: (pwd) => /[^A-Za-z0-9]/.test(pwd) },
]

export function meetsPasswordRequirements(password) {
  return PASSWORD_REQUIREMENTS.every((req) => req.test(password))
}
