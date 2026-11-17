import { reactive } from 'vue'

const state = reactive({
  visible: false,
  title: '',
  value: '',
  placeholder: '',
  resolve: null as ((value: string | null) => void) | null,
})

const prompt = (title: string, initial = '', placeholder = '') => {
  state.title = title
  state.value = initial
  state.placeholder = placeholder
  state.visible = true

  return new Promise<string | null>((res) => {
    state.resolve = res
  })
}

const submit = () => {
  const v = state.value.trim()

  state.visible = false
  state.resolve?.(v || null)
}

const cancel = () => {
  state.visible = false
  state.resolve?.(null)
}

export const usePrompt = () => ({ state, prompt, submit, cancel })
