export type BranchOption = { label: string; value: string }

export type TypedBranchRow = {
  type: 'custom'
  key: string
  label: string
  slot: string
  condition: (context: { query: string }) => boolean
  onClick: (context: { query: string }) => void
}

export type BranchComboboxRow = BranchOption | TypedBranchRow

export const branchComboboxOptions = (
  branchNames: string[],
  selected: string | null | undefined,
  onPick: (branch: string) => void,
): BranchComboboxRow[] => {
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
