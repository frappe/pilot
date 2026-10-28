const STYLE_ID = 'razorpay-checkout-clicks'
const CHECKOUT_SELECTOR = '.razorpay-container'

let stopGuard: (() => void) | undefined

// Checkout is appended to body. The open settings dialog sets pointer-events:none
// on body, which the checkout inherits, and a pointerdown outside the dialog closes it.
export const holdRazorpayCheckout = () => {
  releaseRazorpayCheckout()

  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `${CHECKOUT_SELECTOR}{pointer-events:auto !important}`
  document.head.append(style)

  const guard = (event: Event) => {
    const node = event.target
    if (node instanceof Element && node.closest(CHECKOUT_SELECTOR)) event.stopPropagation()
  }

  document.body.addEventListener('pointerdown', guard)
  stopGuard = () => {
    style.remove()
    document.body.removeEventListener('pointerdown', guard)
    stopGuard = undefined
  }
}

export const releaseRazorpayCheckout = () => stopGuard?.()
