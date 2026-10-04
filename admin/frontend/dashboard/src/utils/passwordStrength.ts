export const PASSWORD_REQUIREMENTS = [
  { label: 'At least 8 characters', test: (pwd: string) => pwd.length >= 8 },

  {
    label: 'Upper & lower case letters',
    test: (pwd: string) => /[a-z]/.test(pwd) && /[A-Z]/.test(pwd),
  },

  { label: 'At least one number', test: (pwd: string) => /\d/.test(pwd) },
  { label: 'At least one symbol', test: (pwd: string) => /[^A-Za-z0-9]/.test(pwd) },
]

export const meetsPasswordRequirements = (password: string) => {
  return PASSWORD_REQUIREMENTS.every((req) => req.test(password))
}
