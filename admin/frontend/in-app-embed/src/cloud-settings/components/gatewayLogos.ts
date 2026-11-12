import RazorpayLogo from '../assets/Razorpay-1.svg?inline'
import StripeLogo from '../assets/Stripe.svg?inline'

/** Logos for the payment gateways Central offers, by adapter key. */
export const GATEWAY_LOGOS: Record<string, string> = { Stripe: StripeLogo, Razorpay: RazorpayLogo }
