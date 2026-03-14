const stripe = require('stripe')(process.env.STRIPE_SK_TEST);

/**
 * Stripe integration for Compliance Platform
 * Handles subscriptions, billing, and feature gating
 */

// Product and Price IDs (update these after creating in Stripe Dashboard)
const PRODUCTS = {
  FREE: 'prod_free', // No Stripe product needed for free
  PRO_MONTHLY: 'price_pro_monthly',
  PRO_ANNUAL: 'price_pro_annual',
  BROKER_MONTHLY: 'price_broker_monthly',
  BROKER_ANNUAL: 'price_broker_annual'
};

/**
 * Create a checkout session for subscription
 */
async function createCheckoutSession(customerEmail, priceId, successUrl, cancelUrl) {
  try {
    const session = await stripe.checkout.sessions.create({
      customer_email: customerEmail,
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      mode: 'subscription',
      success_url: successUrl,
      cancel_url: cancelUrl,
      subscription_data: {
        trial_period_days: 7, // 7-day free trial
      },
    });
    
    return { success: true, sessionId: session.id, url: session.url };
  } catch (error) {
    console.error('Stripe checkout error:', error);
    return { success: false, error: error.message };
  }
}

/**
 * Create Stripe Customer Portal session
 */
async function createPortalSession(customerId, returnUrl) {
  try {
    const session = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: returnUrl,
    });
    
    return { success: true, url: session.url };
  } catch (error) {
    console.error('Stripe portal error:', error);
    return { success: false, error: error.message };
  }
}

/**
 * Handle Stripe webhook events
 */
async function handleWebhookEvent(event) {
  console.log('Stripe webhook:', event.type);
  
  switch (event.type) {
    case 'checkout.session.completed':
      await handleCheckoutCompleted(event.data.object);
      break;
      
    case 'customer.subscription.created':
    case 'customer.subscription.updated':
      await handleSubscriptionUpdated(event.data.object);
      break;
      
    case 'customer.subscription.deleted':
      await handleSubscriptionDeleted(event.data.object);
      break;
      
    case 'invoice.payment_failed':
      await handlePaymentFailed(event.data.object);
      break;
      
    default:
      console.log(`Unhandled event type: ${event.type}`);
  }
  
  return { received: true };
}

/**
 * Handle successful checkout
 */
async function handleCheckoutCompleted(session) {
  const { customer, subscription, metadata } = session;
  
  // Update user in Supabase with subscription info
  console.log('Checkout completed:', { customer, subscription });
  
  // TODO: Update Supabase users table
  // await supabase.from('users').update({
  //   stripe_customer_id: customer,
  //   stripe_subscription_id: subscription,
  //   tier: getTierFromPrice(session.line_items[0].price.id),
  //   status: 'active'
  // }).eq('email', session.customer_email);
}

/**
 * Handle subscription updates
 */
async function handleSubscriptionUpdated(subscription) {
  console.log('Subscription updated:', subscription.id);
  
  // TODO: Update Supabase with new subscription status
  // await supabase.from('subscriptions').upsert({
  //   stripe_subscription_id: subscription.id,
  //   status: subscription.status,
  //   current_period_end: subscription.current_period_end,
  //   cancel_at_period_end: subscription.cancel_at_period_end
  // });
}

/**
 * Handle subscription cancellation
 */
async function handleSubscriptionDeleted(subscription) {
  console.log('Subscription cancelled:', subscription.id);
  
  // TODO: Downgrade user to free tier in Supabase
  // await supabase.from('users').update({
  //   tier: 'free',
  //   status: 'cancelled'
  // }).eq('stripe_subscription_id', subscription.id);
}

/**
 * Handle failed payment
 */
async function handlePaymentFailed(invoice) {
  console.log('Payment failed:', invoice.id);
  
  // TODO: Send email notification, update user status
  // await sendPaymentFailedEmail(invoice.customer_email);
}

/**
 * Get user's subscription tier
 */
function getTierFromPrice(priceId) {
  const tierMap = {
    [PRODUCTS.PRO_MONTHLY]: 'pro',
    [PRODUCTS.PRO_ANNUAL]: 'pro',
    [PRODUCTS.BROKER_MONTHLY]: 'broker',
    [PRODUCTS.BROKER_ANNUAL]: 'broker'
  };
  
  return tierMap[priceId] || 'free';
}

/**
 * Check if user can perform scan based on tier
 */
function canPerformScan(userTier, scansThisMonth) {
  const limits = {
    free: 2,
    pro: Infinity,
    broker: Infinity
  };
  
  return scansThisMonth < limits[userTier];
}

module.exports = {
  createCheckoutSession,
  createPortalSession,
  handleWebhookEvent,
  canPerformScan,
  PRODUCTS
};
