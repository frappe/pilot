import type { ButtonProps } from 'frappe-ui'
import type { Component } from 'vue'

export interface ActionMenuOption {
  label: string
  icon?: Component
  theme?: ButtonProps['theme']
  onClick?: () => void
}
