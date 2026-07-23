import { reactive } from 'vue'

const state = reactive({
  visible: false,
  title: '',
  value: '',
  placeholder: '',
  resolve: null,
})

export function usePrompt() {
  function prompt(title, initial = '', placeholder = '') {
    state.title = title
    state.value = initial
    state.placeholder = placeholder
    state.visible = true
    return new Promise((res) => {
      state.resolve = res
    })
  }
  function submit() {
    const v = state.value.trim()
    state.visible = false
    state.resolve?.(v || null)
  }
  function cancel() {
    state.visible = false
    state.resolve?.(null)
  }
  return { state, prompt, submit, cancel }
}
