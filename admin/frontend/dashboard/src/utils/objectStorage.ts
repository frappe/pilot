export interface RegionOption {
  label: string
  value: string
}

/** The provider's regions, plus the saved region when a custom endpoint serves one the list does not know. */
export const regionOptionsFor = (
  regions: string[],
  endpointUrl: string,
  region: string,
): RegionOption[] => {
  const known = regions.map((name) => ({ label: name, value: name }))
  if (!endpointUrl || !region || regions.includes(region)) return known
  return [...known, { label: region, value: region }]
}
