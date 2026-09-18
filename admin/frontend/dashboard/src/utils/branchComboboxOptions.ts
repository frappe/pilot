export const branchComboboxOptions = (branchNames, selected, onPick) => {
  const options = branchNames.map((name) => ({ label: name, value: name }))
  if (selected && !branchNames.includes(selected)) {
    options.unshift({ label: selected, value: selected })
  }
  return [
    ...options,
    {
      type: 'custom',
      key: 'typed-branch',
      label: 'Use typed branch',
      slot: 'typed-branch',
      condition: ({ query }) => {
        const typed = query.trim()
        return Boolean(typed) && !options.some((option) => option.value === typed)
      },
      onClick: ({ query }) => {
        const typed = query.trim()
        if (typed) onPick(typed)
      },
    },
  ]
}
