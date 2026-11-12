import { onBeforeUnmount, onMounted, ref } from 'vue'

// Desk sets `data-theme="dark"`. Tailwind apps, such as Raven, set the `dark` class.
const isPageDark = () =>
  document.documentElement.dataset.theme === 'dark' ||
  document.documentElement.classList.contains('dark')

/** Whether the host page is dark, kept current while the component is mounted. */
export const usePageDark = () => {
  const pageDark = ref(isPageDark())
  let themeWatcher: MutationObserver | undefined

  onMounted(() => {
    themeWatcher = new MutationObserver(() => {
      pageDark.value = isPageDark()
    })

    themeWatcher.observe(document.documentElement, {
      attributeFilter: ['data-theme', 'class'],
    })
  })

  onBeforeUnmount(() => themeWatcher?.disconnect())

  return pageDark
}
