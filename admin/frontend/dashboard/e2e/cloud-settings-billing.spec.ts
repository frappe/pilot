import { expect, test, type Page } from '@playwright/test'

const PROGRESS_URL = '/tests/host.html?progress=1'

// These follow Central's billing_api responses, which Pilot and Frappe pass through unchanged.
const trial = (state: 'Active' | 'Ended') => ({
  state,
  ends_on: '2026-10-21',
  days_left: state === 'Active' ? 5 : 0,
  deletes_on: '2026-10-28',
  credit: state === 'Active' ? { amount: 10, currency: 'USD', validity_days: 60, ends_on: '2026-10-21' } : null,
  sleeps_after_minutes: 10,
})

const billing = ({ onTrial = 'Active' as 'Active' | 'Ended' | null, hasCard = false } = {}) => ({
  currency: 'USD',
  plan: { name: 'Starter', subtitle: '1 vCPU · 2 GB RAM · 25 GB disk', specs: {} },
  estimate: { amount: '$0.00', note: 'Due Nov 1 · 16d left' },
  credit: { amount: '$0.00', note: 'Available credit', warning: false },
  payment_method: hasCard ? { name: 'pm-1', label: 'Visa •••• 4242' } : null,
  profile_complete: true,
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

test('an active trial names the credit deadline and the idle sleep', async ({ page }) => {
  await openBilling(page, { get_billing: () => ({ message: billing() }), get_account_url: accountLinks })

  await expect(page.getByText(/Your free trial ends in 5 days\. Add a payment method by .+ to keep this site running and get .*10.* credit\./)).toBeVisible()
  await expect(page.getByText('Until you add a payment method, your site sleeps after 10 minutes without visitors. The next visit wakes it in a few seconds.')).toBeVisible()
  await expect(page.getByText('Credit', { exact: true })).toBeVisible()
  await expect(page.getByText('Trial credit')).toHaveCount(0)
})

test('an ended trial names the deletion date, with no credit and no sleep note', async ({ page }) => {
  await openBilling(page, { get_billing: () => ({ message: billing({ onTrial: 'Ended' }) }), get_account_url: accountLinks })

  await expect(page.getByText(/Your free trial has ended and this site is stopped\. Add a payment method before .+ to keep it\./)).toBeVisible()
  await expect(page.getByText(/sleeps after/)).toHaveCount(0)
})

test('during a trial, Change plan asks for a payment method and opens the card flow', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing() }),
    get_account_url: accountLinks,
    get_payment_gateways: () => ({ message: [{ name: 'Stripe', label: 'Stripe', adapter_key: 'Stripe', method_types: ['Card'] }] }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await expect(page.getByText('Your site is on a free trial. Add a payment method to keep it and choose a bigger plan.')).toBeVisible()
  expect(calls.some((call) => call.method === 'get_plan_options')).toBe(false)

  await page.getByRole('button', { name: 'Add payment method' }).first().click()
  await expect(page.getByText('Visa, Mastercard, RuPay, Amex')).toBeVisible()
})

test('a paid site moves to a bigger plan and opens the progress page', async ({ page }) => {
  const calls = await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
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
    get_plan_options: () => ({ message: plans(false) }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('radio', { name: /Pro/ }).check()

  await expect(page.getByText('Your spending limit does not allow a bigger plan. Raise it in Frappe Cloud.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Move to Pro' })).toBeDisabled()
})

test('a failed plan list offers a retry, and See more plans opens Central', async ({ page }) => {
  let attempts = 0
  await openBilling(page, {
    get_billing: () => ({ message: billing({ onTrial: null, hasCard: true }) }),
    get_account_url: accountLinks,
    get_plan_options: () => (++attempts === 1 ? { status: 503, body: {} } : { message: plans() }),
  })

  await page.getByRole('button', { name: 'Change plan' }).click()
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByText('Current')).toBeVisible()

  // Record the link instead of opening it; the test domain does not exist.
  await page.evaluate(() => {
    window.open = (url) => ((window as unknown as { opened: unknown }).opened = String(url)) && null
  })
  await page.getByRole('button', { name: 'See more plans in Frappe Cloud' }).click()
  expect(await page.evaluate(() => (window as unknown as { opened: string }).opened)).toBe('https://cloud.example.test/plans')
})
