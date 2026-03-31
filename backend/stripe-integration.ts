// stripe-integration.ts
// ComplyWithJudy — Stripe checkout + webhook handler
// Deploy as a Netlify Function: /netlify/functions/stripe.ts

import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, { apiVersion: '2024-06-20' });
const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
);

// ----------------------------------------------------------------
// Pricing catalogue  (annual model)
// Fill STRIPE_PRICE_* env vars after creating products in Stripe dashboard
// ----------------------------------------------------------------
export const PLANS = {
  starter: {
    name:           'Starter',
    price:          2900,          // $29.00 in cents
    interval:       'year',
    scans:          5,
    domains:        1,
    stripe_price_id: process.env.STRIPE_PRICE_STARTER!,
    description:    '5 compliance scans per year',
  },
  professional: {
    name:           'Professional',
    price:          7900,          // $79.00
    interval:       'year',
    scans:          25,
    domains:        1,
    stripe_price_id: process.env.STRIPE_PRICE_PROFESSIONAL!,
    description:    '25 compliance scans per year',
  },
  broker: {
    name:           'Broker / Team',
    price:          19900,         // $199.00
    interval:       'year',
    scans:          null,          // unlimited
    domains:        10,
    stripe_price_id: process.env.STRIPE_PRICE_BROKER!,
    description:    'Unlimited scans, 10 domains, team dashboard',
  },
  single: {
    name:           'Single Scan',
    price:          1900,          // $19.00
    interval:       null,          // one-time
    scans:          1,
    domains:        1,
    stripe_price_id: process.env.STRIPE_PRICE_SINGLE!,
    description:    'One full compliance scan with fix instructions',
  },
} as const;

export type PlanKey = keyof typeof PLANS;

// ----------------------------------------------------------------
// Create Checkout Session
// ----------------------------------------------------------------
export async function createCheckoutSession(params: {
  plan:        PlanKey;
  userId?:     string;
  email?:      string;
  scanId?:     string;   // for single-scan post-purchase attribution
  successUrl:  string;
  cancelUrl:   string;
}): Promise<string> {
  const plan = PLANS[params.plan];

  const isSubscription = plan.interval === 'year';

  const session = await stripe.checkout.sessions.create({
    mode:                isSubscription ? 'subscription' : 'payment',
    payment_method_types: ['card'],
    customer_email:      params.email,

    line_items: [{
      price:    plan.stripe_price_id,
      quantity: 1,
    }],

    // After payment, redirect with session_id for verification
    success_url: `${params.successUrl}?session_id={CHECKOUT_SESSION_ID}&plan=${params.plan}`,
    cancel_url:  params.cancelUrl,

    metadata: {
      plan:    params.plan,
      user_id: params.userId  || '',
      scan_id: params.scanId  || '',
    },

    // Allow annual payment with a 'save card' message
    subscription_data: isSubscription ? {
      metadata: { plan: params.plan, user_id: params.userId || '' },
    } : undefined,

    // Billing address — useful for tax
    billing_address_collection: 'auto',

    // Show annual savings messaging
    custom_text: {
      submit: {
        message: isSubscription
          ? `You're subscribing to ${plan.name} — $${plan.price / 100}/year. Cancel anytime.`
          : `One-time payment of $${plan.price / 100}. Includes fix instructions.`,
      }
    },
  });

  return session.url!;
}

// ----------------------------------------------------------------
// Verify checkout success  (called on success_url page load)
// ----------------------------------------------------------------
export async function verifyCheckoutSession(sessionId: string): Promise<{
  plan: PlanKey;
  userId: string;
  customerId: string;
}> {
  const session = await stripe.checkout.sessions.retrieve(sessionId, {
    expand: ['subscription', 'payment_intent'],
  });

  if (session.payment_status !== 'paid') {
    throw new Error('Payment not completed');
  }

  return {
    plan:       session.metadata!.plan as PlanKey,
    userId:     session.metadata!.user_id,
    customerId: session.customer as string,
  };
}

// ----------------------------------------------------------------
// Webhook handler  (receives events from Stripe)
// Endpoint: POST /api/stripe-webhook
// ----------------------------------------------------------------
export async function handleStripeWebhook(
  rawBody:   Buffer | string,
  signature: string
): Promise<{ received: boolean }> {

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(
      rawBody,
      signature,
      process.env.STRIPE_WEBHOOK_SECRET!
    );
  } catch (err) {
    throw new Error(`Webhook signature verification failed: ${err}`);
  }

  // Idempotency — ignore events we've already processed
  const { data: existing } = await supabase
    .from('stripe_events')
    .select('id')
    .eq('id', event.id)
    .single();

  if (existing) {
    return { received: true };   // already handled
  }

  // Record event first (idempotency log)
  await supabase.from('stripe_events').insert({
    id:      event.id,
    type:    event.type,
    payload: event.data.object,
  });

  // Handle events
  switch (event.type) {

    case 'checkout.session.completed': {
      const session = event.data.object as Stripe.CheckoutSession;
      await activateSubscription(session);
      break;
    }

    case 'invoice.payment_succeeded': {
      // Annual renewal
      const invoice = event.data.object as Stripe.Invoice;
      await renewSubscription(invoice);
      break;
    }

    case 'invoice.payment_failed': {
      const invoice = event.data.object as Stripe.Invoice;
      await markSubscriptionPastDue(invoice.subscription as string);
      break;
    }

    case 'customer.subscription.deleted': {
      const sub = event.data.object as Stripe.Subscription;
      await cancelSubscription(sub.id);
      break;
    }

    case 'customer.subscription.updated': {
      const sub = event.data.object as Stripe.Subscription;
      await syncSubscriptionStatus(sub);
      break;
    }
  }

  return { received: true };
}

// ----------------------------------------------------------------
// Subscription lifecycle helpers
// ----------------------------------------------------------------
async function activateSubscription(session: Stripe.CheckoutSession) {
  const plan   = session.metadata!.plan as PlanKey;
  const userId = session.metadata!.user_id;
  if (!userId) return;   // anon single-scan — no subscription row needed

  const planConfig = PLANS[plan];
  const isAnnual   = planConfig.interval === 'year';

  // Upsert subscription row
  await supabase.from('user_subscriptions').upsert({
    user_id:              userId,
    plan,
    status:              'active',
    stripe_customer_id:  session.customer as string,
    stripe_sub_id:       isAnnual ? (session.subscription as string) : null,
    stripe_price_id:     planConfig.stripe_price_id,
    scans_remaining:     planConfig.scans,    // null = unlimited
    domains_allowed:     planConfig.domains,
    current_period_end:  isAnnual
      ? new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString()
      : null,
    updated_at:          new Date().toISOString(),
  }, { onConflict: 'user_id' });

  // Upsert email lead as converted
  await supabase.from('email_leads').upsert({
    email:     session.customer_email || '',
    converted: true,
    source:    'checkout',
  }, { onConflict: 'email' });
}

async function renewSubscription(invoice: Stripe.Invoice) {
  const subId = invoice.subscription as string;
  const stripeSub = await stripe.subscriptions.retrieve(subId);

  await supabase
    .from('user_subscriptions')
    .update({
      status:            'active',
      current_period_end: new Date(stripeSub.current_period_end * 1000).toISOString(),
      // Reset scan counts on renewal
      scans_remaining:   resetScansForPlan(stripeSub.metadata?.plan as PlanKey),
      updated_at:        new Date().toISOString(),
    })
    .eq('stripe_sub_id', subId);
}

function resetScansForPlan(plan: PlanKey): number | null {
  return PLANS[plan]?.scans ?? null;
}

async function markSubscriptionPastDue(subId: string) {
  await supabase
    .from('user_subscriptions')
    .update({ status: 'past_due', updated_at: new Date().toISOString() })
    .eq('stripe_sub_id', subId);
}

async function cancelSubscription(subId: string) {
  await supabase
    .from('user_subscriptions')
    .update({ status: 'canceled', updated_at: new Date().toISOString() })
    .eq('stripe_sub_id', subId);
}

async function syncSubscriptionStatus(sub: Stripe.Subscription) {
  await supabase
    .from('user_subscriptions')
    .update({
      status:               sub.status,
      cancel_at_period_end: sub.cancel_at_period_end,
      current_period_end:   new Date(sub.current_period_end * 1000).toISOString(),
      updated_at:           new Date().toISOString(),
    })
    .eq('stripe_sub_id', sub.id);
}
