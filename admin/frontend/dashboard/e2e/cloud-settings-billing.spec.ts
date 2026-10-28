import { expect, test, type Page } from '@playwright/test'

const PROGRESS_URL = '/tests/host.html?progress=1'

// These follow Central's billing_api responses, which Pilot and Frappe pass through unchanged.
const trial = (state: 'Active' | 'Ended') => ({
  state,
  ends_on: '2026-10-21',
  days_left: state === 'Active' ? 5 : 0,
  deletes_on: '2026-10-28',
})

const billing = ({ onTrial = 'Active' as 'Active' | 'Ended' | null, hasCard = false, profileComplete = true } = {}) => ({
  currency: 'USD',
  plan: { name: 'Starter', subtitle: '1 vCPU · 2 GB RAM · 25 GB disk', specs: {} },
  estimate: { amount: '$0.00', note: 'Due Nov 1 · 16d left' },
  credit: { amount: '$0.00', note: 'Available credit', warning: false },
  payment_method: hasCard ? { name: 'pm-1', label: 'Visa •••• 4242' } : null,
  profile_complete: profileComplete,
  trial: onTrial ? trial(onTrial) : null,
})

const plans = (sufficient = true) => ({
  currency: 'USD',
  provider: 'Scaleway',
  region: 'Paris',
  current: 'starter',
  sufficient,
  plans: [
    { name: 'starter', title: 'Starter', subtitle: '1 vCPU · 2 GB RAM · 25 GB disk', price: '$18/mo', is_current: true },
    { name: 'pro', title: 'Pro', subtitle: '2 vCPU · 4 GB RAM · 40 GB disk', price: '$36/mo', is_current: false },
  ],
})

const trialPlans = () => ({
  message: {
    currency: 'USD',
    provider: 'Scaleway',
    region: 'Paris',
    current: null,
    selected: 'starter',
    sufficient: true,
    plans: [
      { name: 'starter', title: 'Starter', subtitle: '0.5 vCPU · 2 GB RAM · 40 GB SSD', price: '$20/mo', is_current: false },
      { name: 'pro', title: 'Pro', subtitle: '1 vCPU · 4 GB RAM · 80 GB SSD', price: '$40/mo', is_current: false },
    ],
  },
})

const upgrade = {
  get_plan_options: trialPlans,
  change_plan: () => ({ message: { queued: false, resized: false, progress_url: null } }),
}

async function chooseTrialPlan(page: Page) {
  await page.getByRole('button', { name: 'Upgrade' }).click()
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
}

type Responses = Record<string, (form: URLSearchParams) => { status?: number; message?: unknown; body?: object }>

async function openBilling(page: Page, responses: Responses) {
  const calls: { method: string; form: URLSearchParams }[] = []
  await page.route('**/api/method/**', async (route) => {
    const method = new URL(route.request().url()).pathname.split('.').pop()!
    const form = new URLSearchParams(route.request().postData() || '')
    calls.push({ method, form })
    const answer = responses[method]?.(form) ?? { message: {} }
    await route.fulfill({ status: answer.status ?? 200, json: answer.body ?? { message: answer.message } })
  })
  await page.goto('/tests/host.html')
  await page.evaluate(async () => {
    const { mountCloudSettings } = await import('/src/cloud-settings/runtime.ts')
    mountCloudSettings({ enabled: true, site_name: 'example.test' }, { panels: ['billing'] })
  })
  return calls
}

const accountLinks = () => ({
  message: { url: 'https://cloud.example.test', billing: '', change_plan: 'https://cloud.example.test/plans', server: '' },
})

const stripeOnly = () => ({ message: [{ name: 'Stripe', label: 'Stripe', adapter_key: 'Stripe', method_types: ['Card'] }] })

const emptyProfile = () => ({
  message: { currency: '', legal_name: '', email: '', address_line1: '', city: '', state: '', country: '', pincode: '', gstin: '', supported_currencies: ['USD', 'INR'] },
})

test('an active trial shows the days left above the plan, with one primary action', async ({ page }) => {
  await openBilling(page, { get_billing: () => ({ message: billing() }), get_account_url: accountLinks })

  await expect(page.getByText('Your free trial ends in 5 days')).toBeVisible()
  await expect(page.getByText(/Your site is free until .+\. Upgrade to a paid plan to keep it running after that\./)).toBeVisible()
  await expect(page.getByText(/credit/i)).toHaveCount(0)
  await expect(page.getByText('Trial plan')).toBeVisible()
  await expect(page.getByText('Starter')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Upgrade' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Change plan' })).toHaveCount(0)
  await expect(page.getByText('Estimated this cycle')).toHaveCount(0)
  await expect(page.getByText(/sleeps/)).toHaveCount(0)
})

test('an ended trial names the deletion date', async ({ page }) => {
  await openBilling(page, { get_billing: () => ({ message: billing({ onTrial: 'Ended' }) }), get_account_url: accountLinks })

  await expect(page.getByText('Your free trial has ended')).toBeVisible()
  await expect(page.getByText(/This site is stopped\. To keep the site and its data, upgrade to a paid plan before .+\./)).toBeVisible()
})

test('an upgrade picks the plan first, with the cheapest one selected', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: stripeOnly,
  })

  await page.getByRole('button', { name: 'Upgrade' }).click()
  await expect(page.getByText('Step 1 of 2')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Choose your plan' })).toBeVisible()
  await expect(page.getByRole('radio', { name: /Starter/ })).toBeChecked()
  await expect(page.getByText(/Larger plans permanently increase/)).toHaveCount(0)

  await page.getByRole('radio', { name: /Pro/ }).check()
  await page.getByRole('button', { name: 'Continue', exact: true }).click()

  await expect(page.getByText('Step 2 of 2')).toBeVisible()
  await expect(page.getByText('Upgrading to', { exact: true })).toBeVisible()
  await expect(page.getByText('· $40/mo')).toBeVisible()
  expect(calls.find((call) => call.method === 'change_plan')?.form.get('plan')).toBe('pro')
})

test('an upgrade asks for billing details after the plan, then shows only the payment step', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing({ profileComplete: false }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_billing_profile: emptyProfile,
    save_billing_profile: () => ({ message: { saved: true } }),
    get_payment_gateways: stripeOnly,
  })

  await chooseTrialPlan(page)
  await expect(page.getByText('Step 2 of 3')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upgrade' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await expect(page.getByText('Enter the name to show on invoices.')).toBeVisible()
  expect(calls.some((call) => call.method === 'save_billing_profile')).toBe(false)

  await page.getByLabel('Legal name').fill('Acme Inc')
  await page.getByLabel('Address').fill('1 Main St')
  await page.getByLabel('City').fill('Austin')
  await page.getByLabel('Country').fill('United States')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()

  await expect(page.getByText('Step 3 of 3')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue with Stripe' })).toBeVisible()
  await expect(page.getByText('UPI')).toHaveCount(0)
  expect(calls.find((call) => call.method === 'save_billing_profile')?.form.get('currency')).toBe('USD')
})

test('India asks for the state and offers GSTIN', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing({ profileComplete: false }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_billing_profile: emptyProfile,
  })

  await chooseTrialPlan(page)
  await expect(page.getByLabel('GSTIN', { exact: true })).toHaveCount(0)

  await page.getByLabel('Country').fill('India')
  await expect(page.getByLabel('GSTIN', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'State (required)', exact: true })).toBeVisible()
})

test('with lists from Central, country and an Indian state are picked from dropdowns', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing({ profileComplete: false }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_billing_profile: () => ({
      message: { ...emptyProfile().message, countries: ['India', 'United States'], india_states: ['Karnataka', 'Maharashtra'] },
    }),
    save_billing_profile: () => ({ message: { saved: true } }),
    get_payment_gateways: stripeOnly,
  })

  await chooseTrialPlan(page)
  await page.getByLabel('Legal name').fill('Acme Pvt Ltd')
  await page.getByLabel('Address').fill('1 MG Road')
  await page.getByLabel('City').fill('Bengaluru')
  await page.getByLabel('Country').click()
  await page.getByRole('option', { name: 'India', exact: true }).click()
  await page.getByLabel('State').click()
  await page.getByRole('option', { name: 'Karnataka', exact: true }).click()
  await page.getByLabel('Country').click()
  await page.getByRole('option', { name: 'United States', exact: true }).click()
  await expect(page.getByLabel('State')).toHaveValue('')
  await page.getByLabel('Country').click()
  await page.getByRole('option', { name: 'India', exact: true }).click()
  await page.getByLabel('State').click()
  await page.getByRole('option', { name: 'Karnataka', exact: true }).click()
  await page.getByRole('button', { name: 'Continue', exact: true }).click()

  await expect(page.getByText('Step 3 of 3')).toBeVisible()
  const saved = calls.find((call) => call.method === 'save_billing_profile')?.form
  expect(saved?.get('country')).toBe('India')
  expect(saved?.get('state')).toBe('Karnataka')
  expect(saved?.get('currency')).toBe('INR')
})

test('a paid site moves to a bigger plan and opens the progress page', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_plan_options: () => ({ message: plans() }),
    change_plan: () => ({ message: { subscription: 'sub-1', queued: true, resized: true, action: 'ra-1', progress_url: PROGRESS_URL } }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await expect(page.getByText('Current')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Choose a plan' })).toBeDisabled()

  await page.getByRole('radio', { name: /Pro/ }).check()
  await expect(page.getByText('Your site goes offline for a few minutes while it moves to Pro. A progress page brings you back when it is ready.')).toBeVisible()
  await page.getByRole('button', { name: 'Move to Pro' }).click()

  await expect(page).toHaveURL(/progress=1/)
  expect(calls.find((call) => call.method === 'change_plan')?.form.get('plan')).toBe('pro')
})

test('a plan change without a progress page says it has started', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_plan_options: () => ({ message: plans() }),
    change_plan: () => ({ message: { queued: true, resized: true, progress_url: null } }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('radio', { name: /Pro/ }).check()
  await page.getByRole('button', { name: 'Move to Pro' }).click()

  await expect(page.getByText('Your plan change has started. Your site restarts in a few minutes.')).toBeVisible()
})

test("Central's refusal shows beside the plan list", async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_plan_options: () => ({ message: plans() }),
    change_plan: () => ({
      status: 417,
      body: { exc_type: 'ValidationError', _server_messages: JSON.stringify([JSON.stringify({ message: "Add a payment method to change this site's plan." })]) },
    }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('radio', { name: /Pro/ }).check()
  await page.getByRole('button', { name: 'Move to Pro' }).click()

  await expect(page.getByText("Add a payment method to change this site's plan.")).toBeVisible()
  await expect(page).not.toHaveURL(/progress=1/)
})

test('a spending limit blocks a bigger plan and says why', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_plan_options: () => ({ message: plans(false) }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('radio', { name: /Pro/ }).check()

  await expect(page.getByText('Your spending limit does not allow a bigger plan. Raise it in Frappe Cloud.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Move to Pro' })).toBeDisabled()
})

test('a failed plan list offers a retry, and Check more plans opens Central', async ({ page }) => {
  let attempts = 0
  await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_plan_options: () => (++attempts === 1 ? { status: 503, body: {} } : { message: plans() }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByText('Current')).toBeVisible()

  // Record the link instead of opening it; the test domain does not exist.
  await page.evaluate(() => {
    window.open = (url) => ((window as unknown as { opened: unknown }).opened = String(url)) && null
  })
  await page.getByRole('button', { name: 'Check more plans', exact: true }).click()
  expect(await page.evaluate(() => (window as unknown as { opened: string }).opened)).toBe('https://cloud.example.test/plans')
})

test('India requires the state, and a currency the owner picks stays', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing({ profileComplete: false }) }),
    get_account_url: accountLinks,
    ...upgrade,
    get_billing_profile: emptyProfile,
  })

  await chooseTrialPlan(page)
  await page.getByLabel('Currency').click()
  await page.getByRole('option', { name: 'USD' }).click()

  await page.getByLabel('Legal name').fill('Acme Pvt Ltd')
  await page.getByLabel('Address').fill('1 MG Road')
  await page.getByLabel('City').fill('Bengaluru')
  await page.getByLabel('Country').fill('India')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()

  await expect(page.getByText('Choose your state. GST needs it.')).toBeVisible()
  await expect(page.getByLabel('Currency')).toContainText('USD')
  expect(calls.some((call) => call.method === 'save_billing_profile')).toBe(false)
})

test('a trial held by the spending limit says why and offers no payment action', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: { ...billing({ hasCard: true }), trial: { ...trial('Active'), held: true } } }),
    get_account_url: accountLinks,
    ...upgrade,
  })

  await expect(page.getByText('Your site is still on the free trial')).toBeVisible()
  await expect(page.getByText(/above your spending limit\. Choose a smaller plan, or contact support to raise the limit\./)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Choose a plan' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Add payment method' })).toHaveCount(0)
})

test('Razorpay checkout takes clicks while the settings dialog is open', async ({ page }) => {
  await page.route('https://checkout.razorpay.com/v1/checkout.js', (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `window.Razorpay = class {
        constructor(options) { this.options = options }
        on() {}
        open() {
          const root = document.createElement('div')
          root.className = 'razorpay-container'
          root.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#fff;padding:24px'
          root.innerHTML = '<label>Card number <input id="rzp-card"></label><button type="button" id="rzp-pay">Pay</button><button type="button" id="rzp-close">Close checkout</button>'
          root.querySelector('#rzp-close').onclick = () => { root.remove(); this.options.modal.ondismiss() }
          document.body.append(root)
        }
      }`,
    }),
  )
  await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: () => ({ message: [{ name: 'Razorpay', label: 'Razorpay', adapter_key: 'Razorpay', method_types: ['Card'] }] }),
    add_payment_method: () => ({ message: { key_id: 'rzp_test', order_id: 'order_1', customer_id: 'cust_1', payment_method: 'pm-1', prefill: {} } }),
  })

  await chooseTrialPlan(page)
  await page.getByLabel('Phone').fill('9876543210')
  await page.getByRole('button', { name: 'Continue with Razorpay' }).click()

  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).pointerEvents)).toBe('none')
  const card = page.locator('#rzp-card')
  await card.click()
  await expect(card).toBeFocused()
  await expect(page.getByRole('heading', { name: 'Payment method' })).toBeVisible()
  await page.locator('#rzp-pay').click()

  await page.locator('#rzp-close').click()
  await expect(page.getByText('Setup cancelled.')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.getElementById('razorpay-checkout-clicks') !== null)).toBe(false)
})

test('Razorpay explains its mandate limit for a card and for UPI', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: () => ({ message: [{ name: 'Razorpay', label: 'Razorpay', adapter_key: 'Razorpay', method_types: ['Card', 'UPI Autopay'] }] }),
  })

  await chooseTrialPlan(page)
  await expect(page.getByText('Razorpay shows a high limit for card payments.')).toBeVisible()
  await page.getByText('UPI', { exact: true }).click()
  await expect(page.getByText('Razorpay asks you to approve payments up to your spending limit.')).toBeVisible()
})

test('Razorpay needs a phone number before it opens', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: () => ({ message: [{ name: 'Razorpay', label: 'Razorpay', adapter_key: 'Razorpay', method_types: ['Card', 'UPI Autopay'] }] }),
  })

  await chooseTrialPlan(page)
  await expect(page.getByText('UPI', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Continue with Razorpay' }).click()

  await expect(page.getByText('Enter a phone number. Razorpay needs it to save a card.')).toBeVisible()
  expect(calls.some((call) => call.method === 'add_payment_method')).toBe(false)
})

test('a card that Central activates on return shows without Check status', async ({ page }) => {
  let reconciled = false
  await openBilling(page, {
    get_billing: () => ({ message: reconciled ? billing({ onTrial: null, hasCard: true }) : billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    reconcile_payment_setup: () => {
      reconciled = true
      return { message: { activated: 1 } }
    },
  })

  await expect(page.getByText('Visa •••• 4242')).toBeVisible()
  await expect(page.getByText(/Cannot read properties/)).toHaveCount(0)
})

test('a card that fails verification says so and offers a fresh start', async ({ page }) => {
  await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: stripeOnly,
    create_payment_method_checkout: () => ({ message: { checkout_url: 'https://checkout.example.test/1', reference: 'ref-1' } }),
    confirm_payment_method_checkout: () => ({
      message: { status: 'Failed', active: false, message: 'We could not verify this card. Try another card.' },
    }),
  })
  await page.evaluate(() => {
    window.open = () => null
  })

  await chooseTrialPlan(page)
  await page.getByRole('button', { name: 'Continue with Stripe' }).click()
  await page.getByRole('button', { name: 'Check status' }).click()

  await expect(page.getByText('We could not verify this card. Try another card.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Check status' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Continue with Stripe' })).toBeEnabled()
})

test('a payment method that converts the trial opens the progress page of its move', async ({ page }) => {
  let converted = false
  const moving = { ...billing({ onTrial: null, hasCard: true }), plan_change: { progress_url: PROGRESS_URL } }
  await openBilling(page, {
    get_billing: () => ({ message: converted ? moving : billing() }),
    get_account_url: accountLinks,
    ...upgrade,
    get_payment_gateways: stripeOnly,
    create_payment_method_checkout: () => ({ message: { checkout_url: 'https://checkout.example.test/1', reference: 'ref-1' } }),
    confirm_payment_method_checkout: () => {
      converted = true
      return { message: { status: 'Active', active: true } }
    },
  })
  await page.evaluate(() => {
    window.open = () => null
  })

  await chooseTrialPlan(page)
  await page.getByRole('button', { name: 'Continue with Stripe' }).click()
  await page.getByRole('button', { name: 'Check status' }).click()

  await expect(page).toHaveURL(/progress=1/)
})

test('a move that is still running opens its progress page when billing opens again', async ({ page }) => {
  const moving = { ...billing({ onTrial: null, hasCard: true }), plan_change: { plan: 'Pro', progress_url: PROGRESS_URL } }
  await openBilling(page, { get_billing: () => ({ message: moving }), get_account_url: accountLinks })

  await expect(page).toHaveURL(/progress=1/)
})
