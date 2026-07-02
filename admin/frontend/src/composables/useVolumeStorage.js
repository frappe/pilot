import { ref, computed, watch } from 'vue'

const GIB = 1024 ** 3
const CUSTOM_DEVICE = '__custom__'
const UNITS = { K: 1024, M: 1024 ** 2, G: GIB, T: 1024 ** 4, P: 1024 ** 5 }

function parseSize(value) {
  const match = String(value).trim().toUpperCase().match(/^([1-9]\d*)\s*([KMGTP])$/)
  return match ? parseInt(match[1], 10) * UNITS[match[2]] : null
}

function toWholeGiB(bytes) {
  return `${Math.max(1, Math.floor(bytes / GIB))}G`
}

function deviceLabel(device) {
  const sizeGiB = Math.floor(device.size_bytes / GIB)
  const note = device.pool ? ', in use' : device.has_signature ? ', has data' : ''
  return `${device.path} (${sizeGiB} GB${note})`
}

export function useVolumeStorage(backing, device, imageSize) {
  const availableDevices = ref([])
  const rootfsFreeBytes = ref(0)
  const customDevice = ref(false)

  const deviceOptions = computed(() => [
    ...availableDevices.value.map((d) => ({ label: deviceLabel(d), value: d.path })),
    { label: 'Other disk…', value: CUSTOM_DEVICE },
  ])
  const showDeviceDropdown = computed(
    () => availableDevices.value.length > 0 && !customDevice.value,
  )

  const freeGiB = computed(() => Math.floor(rootfsFreeBytes.value / GIB))
  const imageSizeMaxGiB = computed(() => Math.max(5, freeGiB.value || 100))
  const imageSizeMinGiB = computed(() => Math.min(5, imageSizeMaxGiB.value))
  const imageSizeGiB = computed(() => parseInt(imageSize.value) || imageSizeMinGiB.value)

  function clamp(value) {
    return Math.min(imageSizeMaxGiB.value, Math.max(imageSizeMinGiB.value, value))
  }

  const imageSliderModel = computed({
    get: () => [clamp(imageSizeGiB.value)],
    set: ([value]) => {
      imageSize.value = `${value}G`
    },
  })

  function clampImageSize() {
    imageSize.value = `${clamp(imageSizeGiB.value)}G`
  }

  // Reservation and quota are derived from the backing size at request time.
  function backingSizeBytes() {
    if (backing.value !== 'device') return parseSize(imageSize.value)
    const found = availableDevices.value.find((d) => d.path === device.value)
    return found ? found.size_bytes : null
  }

  const volumeSizes = computed(() => {
    const bytes = backingSizeBytes()
    if (!bytes) return { volume_reservation: '15G', volume_quota: '60G' }
    return { volume_reservation: toWholeGiB(bytes * 0.15), volume_quota: toWholeGiB(bytes) }
  })

  // The "Other disk…" sentinel switches the picker to a free-text field.
  watch(device, (value) => {
    if (value !== CUSTOM_DEVICE) return
    customDevice.value = true
    device.value = ''
  })

  return {
    availableDevices,
    rootfsFreeBytes,
    deviceOptions,
    showDeviceDropdown,
    freeGiB,
    imageSizeGiB,
    imageSizeMinGiB,
    imageSizeMaxGiB,
    imageSliderModel,
    volumeSizes,
    clampImageSize,
  }
}
