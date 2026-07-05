<script setup>
import { Button, Dropdown } from 'frappe-ui'
import { useI18n } from '@/i18n'

const { locale, localeLabel, setLocale, t } = useI18n()

const options = () => [
  { label: t('common.english'), onClick: () => setLocale('en'), disabled: locale.value === 'en' },
  { label: t('common.chinese'), onClick: () => setLocale('zh-CN'), disabled: locale.value === 'zh-CN' },
]
</script>

<template>
  <Dropdown :options="options()" placement="bottom-end">
    <template #default="{ open }">
      <Button variant="ghost" :active="open" :label="localeLabel">
        <template #prefix><span class="size-4 lucide-languages" /></template>
      </Button>
    </template>
  </Dropdown>
</template>
