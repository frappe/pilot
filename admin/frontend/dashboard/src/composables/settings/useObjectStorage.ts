import { computed, ref, watch } from 'vue'
import { toast } from 'frappe-ui'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { settingsApi } from '@/api/settings'
import type { S3ProviderOption } from '@/types/settings'
import { errorMessage } from '@/utils/error'

export const useObjectStorage = () => {
  const loading = ref(true)
  const saving = ref(false)
  const disconnecting = ref(false)
  const error = ref('')
  const accessKey = ref('')
  const secretKey = ref('')
  const bucket = ref('')
  const provider = ref('')
  const region = ref('')
  const endpointUrl = ref('')
  const secretKeySet = ref(false)
  const providers = ref<S3ProviderOption[]>([])
  const loadingRegions = ref(false)

  const connected = computed(() => Boolean(accessKey.value && bucket.value && secretKeySet.value))
  const providerLabel = computed(
    () => providers.value.find((p) => p.value === provider.value)?.label || provider.value,
  )
  const providerOptions = computed(() =>
    providers.value.map((p) => ({ label: p.label, value: p.value })),
  )
  const regionOptions = computed(
    () =>
      providers.value
        .find((p) => p.value === provider.value)
        ?.regions.map((r) => ({ label: r, value: r })) || [],
  )

  const loadFrappeRegions = async () => {
    const frappe = providers.value.find((p) => p.value === 'frappe')

    if (!frappe) return

    loadingRegions.value = true

    try {
      const regions = await settingsApi.frappeStorageRegions()

      if (hasApiError(regions)) {
        error.value = apiErrorMessage(
          regions,
          'Could not read Frappe storage regions from Central.',
        )

        return
      }

      frappe.regions = regions
    } catch (e) {
      error.value = errorMessage(e, 'Could not read Frappe storage regions from Central.')
    } finally {
      loadingRegions.value = false
    }
  }

  watch([provider, providers], async ([current], [previous]) => {
    if (previous && current !== previous) {
      endpointUrl.value = ''
    }

    if (current === 'frappe') {
      await loadFrappeRegions()
    }

    if (!regionOptions.value.some((o) => o.value === region.value)) {
      region.value = regionOptions.value[0]?.value || ''
    }
  })

  const canSave = computed(
    () =>
      Boolean(accessKey.value.trim()) &&
      Boolean(bucket.value.trim()) &&
      Boolean(provider.value) &&
      Boolean(region.value) &&
      (secretKeySet.value || Boolean(secretKey.value.trim())),
  )

  const load = async () => {
    loading.value = true

    try {
      const data = await settingsApi.get()
      const s3 = data.s3 || {}

      providers.value = data.s3_providers || []
      accessKey.value = s3.access_key || ''
      bucket.value = s3.bucket || ''
      provider.value = s3.provider || providers.value[0]?.value || ''
      region.value = s3.region || ''
      endpointUrl.value = s3.endpoint_url || ''
      secretKeySet.value = !!s3.secret_key_set
    } catch (e) {
      error.value = errorMessage(e, 'Could not load settings.')
    } finally {
      loading.value = false
    }
  }

  const save = async () => {
    saving.value = true
    error.value = ''

    try {
      await settingsApi.update({
        s3: {
          access_key: accessKey.value.trim(),
          secret_key: secretKey.value.trim(),
          bucket: bucket.value.trim(),
          provider: provider.value,
          region: region.value,
          endpoint_url: endpointUrl.value.trim(),
        },
      })

      secretKey.value = ''
      toast.success('Object storage settings saved')
      await load()

      return true
    } catch (e) {
      error.value = errorMessage(e, 'Could not save object storage settings.')

      return false
    } finally {
      saving.value = false
    }
  }

  const disconnect = async () => {
    disconnecting.value = true

    try {
      await settingsApi.update({ s3: { disconnect: true } })

      accessKey.value = ''
      secretKey.value = ''
      bucket.value = ''
      provider.value = providers.value[0]?.value || ''
      region.value = ''
      endpointUrl.value = ''
      secretKeySet.value = false

      toast.success('Object storage disconnected')
    } catch (e) {
      toast.error(errorMessage(e, 'Could not disconnect object storage.'))
    } finally {
      disconnecting.value = false
    }
  }

  return {
    loading,
    saving,
    disconnecting,
    error,
    accessKey,
    secretKey,
    bucket,
    provider,
    region,
    endpointUrl,
    secretKeySet,
    providers,
    loadingRegions,
    connected,
    providerLabel,
    providerOptions,
    regionOptions,
    canSave,
    load,
    save,
    disconnect,
  }
}
