// netlify/functions/stripe.ts
// ComplyWithJudy — Stripe checkout, webhook, and lead capture
// Routes: POST /.netlify/functions/stripe/create-checkout
//         POST /.netlify/functions/stripe/capture-lead
//         POST /.netlify/functions/stripe/webhook

import type { Handler, HandlerEvent } from '@netlify/functions';
import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, { apiVersion: '2024-06-20' });

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
);

// ----------------------------------------------------------------
// Pricing catalogue
// ----------------------------------------------------------------
const PLANS: Record<string, {
  name: string;
  price: number;
  interval: string | null;
  scans: number | null;
  domains: number;
  stripe_price_id: string;
}> = {
  starter: {
    name: 'Starter',
    price: 2900,
    interval: 'year',
    scans: 5,
    domains: 1,
    stripe_price_id: process.env.STRIPE_PRICE_STARTER || '',
  },
  professional: {
    name: 'Professional',
    price: 7900,
    interval: 'year',
    scans: 25,
    domains: 1,
    stripe_price_id: process.env.STRIPE_PRICE_PROFESSIONAL || '',
  },
  broker: {
    name: 'Broker / Team',
    price: 19900,
    interval: 'year',
    scans: null,
    domains: 10,
    stripe_price_id: process.env.STRIPE_PRICE_BROKER || '',
  },
  single: {
    name: 'Single Scan',
    price: 1900,
    interval: null,
    scans: 1,
    domains: 1,
    stripe_price_id: process.env.STRIPE_PRICE_SINGLE || '',
  },
};

// ----------------------------------------------------------------
// CORS headers
// ----------------------------------------------------------------
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Content-Type': 'application/json',
};

// ----------------------------------------------------------------
// Route: POST /create-checkout
// ----------------------------------------------------------------
async function handleCreateCheckout(body: {
  plan: string;
  email?: string;
  userId?: string;
  scanId?: string;
  successUrl: string;
  cancelUrl: string;
}) {
  const plan = PLANS[body.plan];
  if (!plan) {
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Invalid plan' }) };
  }

  if (!plan.stripe_price_id) {
    return { statusCode: 500, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Stripe price not configured for this plan' }) };
  }

  const isSubscription = plan.interval === 'year';

  const sessionParams: Stripe.Checkout.SessionCreateParams = {
    mode: isSubscription ? 'subscription' : 'payment',
    payment_method_types: ['card'],
    customer_email: body.email || undefined,
    line_items: [{
      price: plan.stripe_price_id,
      quantity: 1,
    }],
    success_url: `${body.successUrl}?session_id={CHECKOUT_SESSION_ID}&plan=${body.plan}`,
    cancel_url: body.cancelUrl,
    metadata: {
      plan: body.plan,
      user_id: body.userId || '',
      scan_id: body.scanId || '',
    },
    billing_address_collection: 'auto',
    custom_text: {
      submit: {
        message: isSubscription
          ? `You're subscribing to ${plan.name} — $${plan.price / 100}/year. Cancel anytime.`
          : `One-time payment of $${plan.price / 100}. Includes fix instructions and webmaster emails.`,
      },
    },
  };

  if (isSubscription) {
    sessionParams.subscription_data = {
      metadata: { plan: body.plan, user_id: body.userId || '' },
    };
  }

  const session = await stripe.checkout.sessions.create(sessionParams);

  return {
    statusCode: 200,
    headers: CORS_HEADERS,
    body: JSON.stringify({ url: session.url }),
  };
}

// ----------------------------------------------------------------
// Route: POST /capture-lead
// ----------------------------------------------------------------
async function handleCaptureLead(body: { email: string; source?: string }) {
  if (!body.email) {
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Email required' }) };
  }

  try {
    await supabase.from('email_leads').upsert(
      {
        email: body.email.toLowerCase().trim(),
        source: body.source || 'scan',
        converted: false,
      },
      { onConflict: 'email' }
    );
  } catch (e) {
    // Non-critical — don't fail the request
    console.error('Lead capture failed:', e);
  }

  return {
    statusCode: 200,
    headers: CORS_HEADERS,
    body: JSON.stringify({ ok: true }),
  };
}

// ----------------------------------------------------------------
// Route: POST /webhook
// ----------------------------------------------------------------
async function handleWebhook(event: HandlerEvent) {
  const sig = event.headers['stripe-signature'];
  if (!sig || !event.body) {
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Missing signature' }) };
  }

  let stripeEvent: Stripe.Event;
  try {
    stripeEvent = stripe.webhooks.constructEvent(
      event.body,
      sig,
      process.env.STRIPE_WEBHOOK_SECRET!
    );
  } catch (err) {
    console.error('Webhook signature verification failed:', err);
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Invalid signature' }) };
  }

  // Idempotency check
  try {
    const { data: existing } = await supabase
      .from('stripe_events')
      .select('id')
      .eq('id', stripeEvent.id)
      .single();

    if (existing) {
      return { statusCode: 200, headers: CORS_HEADERS, body: JSON.stringify({ received: true }) };
    }

    // Log event
    await supabase.from('stripe_events').insert({
      id: stripeEvent.id,
      type: stripeEvent.type,
      payload: stripeEvent.data.object,
    });
  } catch (e) {
    // Table might not exist yet — proceed anyway
    console.error('Stripe event logging failed:', e);
  }

  // Handle events
  switch (stripeEvent.type) {
    case 'checkout.session.completed': {
      const session = stripeEvent.data.object as Stripe.Checkout.Session;
      await activateSubscription(session);
      break;
    }
    case 'invoice.payment_succeeded': {
      const invoice = stripeEvent.data.object as Stripe.Invoice;
      await renewSubscription(invoice);
      break;
    }
    case 'invoice.payment_failed': {
      const invoice = stripeEvent.data.object as Stripe.Invoice;
      await markSubscriptionPastDue(invoice.subscription as string);
      break;
    }
    case 'customer.subscription.deleted': {
      const sub = stripeEvent.data.object as Stripe.Subscription;
      await cancelSubscription(sub.id);
      break;
    }
    case 'customer.subscription.updated': {
      const sub = stripeEvent.data.object as Stripe.Subscription;
      await syncSubscriptionStatus(sub);
      break;
    }
  }

  return { statusCode: 200, headers: CORS_HEADERS, body: JSON.stringify({ received: true }) };
}

// ----------------------------------------------------------------
// Subscription lifecycle helpers
// ----------------------------------------------------------------
async function activateSubscription(session: Stripe.Checkout.Session) {
  const plan = session.metadata?.plan;
  const userId = session.metadata?.user_id;
  if (!userId || !plan) return;

  const planConfig = PLANS[plan];
  if (!planConfig) return;

  const isAnnual = planConfig.interval === 'year';

  try {
    await supabase.from('user_subscriptions').upsert({
      user_id: userId,
      plan,
      status: 'active',
      stripe_customer_id: session.customer as string,
      stripe_sub_id: isAnnual ? (session.subscription as string) : null,
      stripe_price_id: planConfig.stripe_price_id,
      scans_remaining: planConfig.scans,
      domains_allowed: planConfig.domains,
      current_period_end: isAnnual
        ? new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString()
        : null,
      updated_at: new Date().toISOString(),
    }, { onConflict: 'user_id' });

    // Mark lead as converted
    if (session.customer_email) {
      await supabase.from('email_leads').upsert({
        email: session.customer_email,
        converted: true,
        source: 'checkout',
      }, { onConflict: 'email' });
    }
  } catch (e) {
    console.error('activateSubscription failed:', e);
  }
}

async function renewSubscription(invoice: Stripe.Invoice) {
  const subId = invoice.subscription as string;
  try {
    const stripeSub = await stripe.subscriptions.retrieve(subId);
    const plan = stripeSub.metadata?.plan;

    await supabase
      .from('user_subscriptions')
      .update({
        status: 'active',
        current_period_end: new Date(stripeSub.current_period_end * 1000).toISOString(),
        scans_remaining: plan ? (PLANS[plan]?.scans ?? null) : null,
        updated_at: new Date().toISOString(),
      })
      .eq('stripe_sub_id', subId);
  } catch (e) {
    console.error('renewSubscription failed:', e);
  }
}

async function markSubscriptionPastDue(subId: string) {
  try {
    await supabase
      .from('user_subscriptions')
      .update({ status: 'past_due', updated_at: new Date().toISOString() })
      .eq('stripe_sub_id', subId);
  } catch (e) {
    console.error('markSubscriptionPastDue failed:', e);
  }
}

async function cancelSubscription(subId: string) {
  try {
    await supabase
      .from('user_subscriptions')
      .update({ status: 'canceled', updated_at: new Date().toISOString() })
      .eq('stripe_sub_id', subId);
  } catch (e) {
    console.error('cancelSubscription failed:', e);
  }
}

async function syncSubscriptionStatus(sub: Stripe.Subscription) {
  try {
    await supabase
      .from('user_subscriptions')
      .update({
        status: sub.status,
        current_period_end: new Date(sub.current_period_end * 1000).toISOString(),
        updated_at: new Date().toISOString(),
      })
      .eq('stripe_sub_id', sub.id);
  } catch (e) {
    console.error('syncSubscriptionStatus failed:', e);
  }
}

// ----------------------------------------------------------------
// Main handler — route by path
// ----------------------------------------------------------------
const handler: Handler = async (event) => {
  // Handle CORS preflight
  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers: CORS_HEADERS, body: '' };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  // Extract route from path: /.netlify/functions/stripe/create-checkout -> create-checkout
  const path = event.path.split('/').pop() || '';

  try {
    switch (path) {
      case 'create-checkout': {
        const body = JSON.parse(event.body || '{}');
        return handleCreateCheckout(body);
      }
      case 'capture-lead': {
        const body = JSON.parse(event.body || '{}');
        return handleCaptureLead(body);
      }
      case 'webhook':
        return handleWebhook(event);
      default:
        return { statusCode: 404, headers: CORS_HEADERS, body: JSON.stringify({ error: `Unknown route: ${path}` }) };
    }
  } catch (err) {
    console.error('Stripe function error:', err);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: 'Internal server error' }),
    };
  }
};

export { handler };
