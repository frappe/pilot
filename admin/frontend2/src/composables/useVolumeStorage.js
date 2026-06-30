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

export function useVolumeStorage(form) {
  const availableDevices = ref([])
  const rootfsFreeBytes = ref(0)
  const customDevice = ref(false)

  const deviceOptions = computed(() => [
    ...availableDevices.value.map((device) => ({ label: deviceLabel(device), value: device.path })),
    { label: 'Other disk…', value: CUSTOM_DEVICE },
  ])
  const showDeviceDropdown = computed(
    () => availableDevices.value.length > 0 && !customDevice.value,
  )

  const freeGiB = computed(() => Math.floor(rootfsFreeBytes.value / GIB))
  const imageSizeMaxGiB = computed(() => Math.max(5, freeGiB.value || 100))
  const imageSizeMinGiB = computed(() => Math.min(5, imageSizeMaxGiB.value))
  const imageSizeGiB = computed(
    () => parseInt(form.value.volume_image_size) || imageSizeMinGiB.value,
  )

  function clamp(value) {
    return Math.min(imageSizeMaxGiB.value, Math.max(imageSizeMinGiB.value, value))
  }

  const imageSliderModel = computed({
    get: () => [clamp(imageSizeGiB.value)],
    set: ([value]) => {
      form.value.volume_image_size = `${value}G`
    },
  })

  function clampImageSize() {
    form.value.volume_image_size = `${clamp(imageSizeGiB.value)}G`
  }

  function backingSizeBytes() {
    if (form.value.volume_backing !== 'device') return parseSize(form.value.volume_image_size)
    const device = availableDevices.value.find((d) => d.path === form.value.volume_device)
    return device ? device.size_bytes : null
  }

  function applySmartSizes() {
    const bytes = backingSizeBytes()
    if (!bytes) return
    form.value.volume_quota = toWholeGiB(bytes)
    form.value.volume_reservation = toWholeGiB(bytes * 0.15)
  }

  watch(
    () => form.value.volume_device,
    (value) => {
      if (value !== CUSTOM_DEVICE) return
      customDevice.value = true
      form.value.volume_device = ''
    },
  )
  watch(
    () => [form.value.volume_backing, form.value.volume_device, form.value.volume_image_size],
    applySmartSizes,
  )

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
    clampImageSize,
  }
}
