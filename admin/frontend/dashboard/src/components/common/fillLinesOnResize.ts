import { Transaction } from '@codemirror/state'
import { type EditorView, ViewPlugin } from '@codemirror/view'

/** When the editor grows taller, add empty lines at the end so the new space has lines too. */
export const fillLinesOnResize = ViewPlugin.fromClass(
  class {
    height = 0
    observer: ResizeObserver

    constructor(readonly view: EditorView) {
      this.observer = new ResizeObserver(() => this.fill())
      this.observer.observe(view.scrollDOM)
    }

    fill() {
      const height = this.view.scrollDOM.clientHeight
      // The first size, and a size after being hidden, is not a resize.
      const hasGrown = this.height > 0 && height > this.height
      this.height = height
      if (!hasGrown) return
      const { doc } = this.view.state
      const missing = Math.floor(height / this.view.defaultLineHeight) - 1 - doc.lines
      if (missing <= 0) return
      this.view.dispatch({
        changes: { from: doc.length, insert: '\n'.repeat(missing) },
        annotations: Transaction.addToHistory.of(false),
      })
    }

    destroy() {
      this.observer.disconnect()
    }
  },
)
