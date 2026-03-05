import stripe
import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

class StripeService:
    
    @staticmethod
    def create_payment_intent(
        amount: Decimal,
        currency: str = "usd",
        customer_email: str = None,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        LEGACY: Create a Stripe Payment Intent (one-time charge).
        
        ⚠️  This method is kept for backwards compatibility only.
        For subscriptions, use create_subscription_with_saved_card() instead.
        Payment intents do NOT create a Stripe subscription record, meaning
        Stripe will never auto-renew the charge. This was the root cause of
        missing subscription records in the Stripe dashboard.
        """
        try:
            amount_in_cents = int(amount * 100)
            
            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency=currency,
                receipt_email=customer_email,
                metadata=metadata or {},
                automatic_payment_methods={
                    'enabled': True,
                },
            )
            
            return {
                "clientSecret": intent.client_secret,
                "paymentIntentId": intent.id,
                "amount": amount,
                "currency": currency
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Stripe error: {str(e)}")
    
    @staticmethod
    def verify_payment(payment_intent_id: str) -> Dict[str, Any]:
        """
        Verify a payment intent or setup intent status.
        Used after 3D Secure authentication to confirm the payment succeeded.
        """
        try:
            if payment_intent_id.startswith("seti_"):
                intent = stripe.SetupIntent.retrieve(payment_intent_id)
                return {
                    "status": intent.status,
                    "amount": 0,
                    "currency": "USD",
                    "payment_method": intent.payment_method,
                    "customer_email": None,
                    "metadata": intent.metadata
                }
            else:
                intent = stripe.PaymentIntent.retrieve(payment_intent_id)
                return {
                    "status": intent.status,
                    "amount": intent.amount / 100,
                    "currency": intent.currency,
                    "payment_method": intent.payment_method,
                    "customer_email": intent.receipt_email,
                    "metadata": intent.metadata
                }
        except stripe.error.StripeError as e:
            raise Exception(f"Stripe verification error: {str(e)}")
    
    @staticmethod
    def create_refund(payment_intent_id: str, amount: float = None) -> Dict[str, Any]:
        """Create a refund for a payment."""
        try:
            refund_data = {"payment_intent": payment_intent_id}
            if amount:
                refund_data["amount"] = int(amount * 100)
            refund = stripe.Refund.create(**refund_data)
            return {
                "refund_id": refund.id,
                "status": refund.status,
                "amount": refund.amount / 100
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Refund error: {str(e)}")
    
    @staticmethod
    def verify_webhook_signature(payload: bytes, sig_header: str) -> Dict[str, Any]:
        """Verify Stripe webhook signature."""
        webhook_secret = (
            os.getenv("STRIPE_WEBHOOK_SECRET")
            or os.getenv("STRIPE_CONNECT_WEBHOOK_SECRET")
            or os.getenv("STRIPE_PLATFORM_WEBHOOK_SECRET")
        )
        
        if not webhook_secret:
            raise Exception("STRIPE_WEBHOOK_SECRET is not set in environment variables")
        
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
            return event
        except ValueError:
            raise Exception("Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise Exception("Invalid signature")
    
    # ============================================================================
    # CUSTOMER MANAGEMENT
    # ============================================================================
    
    @staticmethod
    def get_or_create_customer(
        user_id: int,
        email: str,
        name: str,
        stripe_customer_id: Optional[str] = None
    ) -> str:
        """
        Get existing Stripe customer or create a new one.
        Returns the Stripe customer ID (cus_xxx).
        
        Every subscribing user must have a Stripe Customer object so that:
        - Payment methods can be attached and reused
        - Stripe can manage subscription billing cycles automatically
        - Invoices are associated with the correct customer
        """
        try:
            if stripe_customer_id:
                try:
                    customer = stripe.Customer.retrieve(stripe_customer_id)
                    if not getattr(customer, 'deleted', False):
                        return customer.id
                except stripe.error.InvalidRequestError:
                    pass  # Customer deleted or doesn't exist, create new one
            
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={
                    "user_id": str(user_id),
                    "platform": "lavoo_bi"
                }
            )
            return customer.id
            
        except stripe.error.StripeError as e:
            raise Exception(f"Customer creation error: {str(e)}")
    
    @staticmethod
    def attach_payment_method(
        payment_method_id: str,
        customer_id: str,
        set_as_default: bool = True
    ) -> Dict[str, Any]:
        """
        Attach a payment method to a Stripe customer.
        
        Setting as default ensures Stripe uses this card for all future
        automatic subscription renewals without any user intervention.
        """
        try:
            # Attach — idempotent if already attached
            try:
                stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
            except stripe.error.InvalidRequestError as e:
                # Already attached to this customer — that's fine
                if "already been attached" not in str(e):
                    raise
            
            if set_as_default:
                stripe.Customer.modify(
                    customer_id,
                    invoice_settings={"default_payment_method": payment_method_id},
                )
            
            return {
                "status": "success",
                "payment_method_id": payment_method_id,
                "customer_id": customer_id
            }
            
        except stripe.error.StripeError as e:
            raise e
    
    # ============================================================================
    # SUBSCRIPTION MANAGEMENT
    # ============================================================================
    
    @staticmethod
    def create_subscription_with_saved_card(
        customer_id: str,
        price_id: str,
        payment_method_id: str,
        metadata: Dict[str, Any] = None,
        off_session: bool = False
    ) -> Dict[str, Any]:
        """
        Create a Stripe Subscription using a saved payment method.

        This is the CORRECT way to bill recurring subscriptions. Unlike a
        PaymentIntent (one-time charge), a Subscription:
        - Creates a sub_xxx record visible in the Stripe dashboard
        - Automatically generates and pays invoices at each billing cycle
        - Fires invoice.payment_succeeded webhooks on every renewal
        - Handles failed payments with Smart Retries automatically
        - Supports test clock simulation for renewal testing

        payment_behavior is chosen based on whether a user is present:

        off_session=False (default) — user IS present on the checkout page:
            payment_behavior = "default_incomplete"
            Subscription created in 'incomplete' status. Frontend must call
            stripe.confirmCardPayment(client_secret) to confirm the PaymentIntent.
            If not confirmed within 23 hours, Stripe cancels the subscription.
            Use for: /create-subscription-with-saved-card, /save-card-beta (launch)

        off_session=True — no user present (cron / server-side billing):
            payment_behavior = "error_if_incomplete"
            Stripe attempts the charge immediately. Either succeeds → 'active',
            or raises CardError immediately. No incomplete subscription is created,
            so nothing can expire and get cancelled after 23 hours.
            Use for: process_beta_billing.py cron script

        To test 3DS modal: use card 4000 0025 0000 3155 with off_session=False
        To test 3DS decline: use card 4000 0027 6000 3184 with off_session=False
        """
        try:
            print(f"🔵 Creating subscription for customer {customer_id} with price {price_id} "
                  f"(off_session={off_session})")

            payment_behavior = "error_if_incomplete" if off_session else "default_incomplete"

            create_params = dict(
                customer=customer_id,
                items=[{"price": price_id}],
                default_payment_method=payment_method_id,
                payment_behavior=payment_behavior,
                payment_settings={
                    "save_default_payment_method": "on_subscription",
                    "payment_method_types": ["card"]
                },
                metadata=metadata or {},
                expand=["latest_invoice.payment_intent"]
            )

            if off_session:
                create_params["off_session"] = True

            subscription = stripe.Subscription.create(**create_params)
            
            print(f"✅ Subscription created: {subscription.id}, status: {subscription.status}")
            
            client_secret = None
            payment_intent_id = None
            
            # Extract client_secret for 3D Secure authentication
            if subscription.status in ["incomplete", "past_due"]:
                try:
                    latest_invoice = subscription.latest_invoice
                    if latest_invoice and hasattr(latest_invoice, 'payment_intent'):
                        pi = latest_invoice.payment_intent
                        if isinstance(pi, str):
                            # Not expanded — retrieve it
                            payment_intent = stripe.PaymentIntent.retrieve(pi)
                            payment_intent_id = payment_intent.id
                            client_secret = payment_intent.client_secret
                        elif pi:
                            payment_intent_id = pi.id
                            client_secret = pi.client_secret
                except Exception as e:
                    print(f"⚠️ Could not extract client_secret from invoice: {str(e)}")
                
                # Fallback: search recent payment intents for this customer
                if not client_secret:
                    try:
                        recent_intents = stripe.PaymentIntent.list(customer=customer_id, limit=5)
                        for intent in recent_intents.data:
                            if intent.status in ["requires_action", "requires_payment_method", "requires_confirmation"]:
                                payment_intent_id = intent.id
                                client_secret = intent.client_secret
                                break
                    except Exception as e:
                        print(f"⚠️ Fallback payment intent search failed: {str(e)}")
                
                if not client_secret:
                    return {
                        "subscription_id": subscription.id,
                        "status": "requires_action",
                        "client_secret": None,
                        "current_period_end": None,
                        "payment_intent_id": None,
                        "error": "Unable to retrieve payment authentication details. Please try again."
                    }
            
            # Use Stripe's current_period_end as the authoritative end date.
            # Do NOT calculate locally with timedelta — Stripe owns the billing cycle.
            # After expand=["latest_invoice.payment_intent"], the subscription object
            # sometimes doesn't hydrate period fields directly. Retrieve fresh if needed.
            current_period_end = getattr(subscription, 'current_period_end', None)
            current_period_start = getattr(subscription, 'current_period_start', None)

            if subscription.status == "active" and not (current_period_start and current_period_end):
                # Fresh retrieve without expand to get clean period fields
                try:
                    fresh = stripe.Subscription.retrieve(subscription.id)
                    current_period_start = getattr(fresh, 'current_period_start', None)
                    current_period_end = getattr(fresh, 'current_period_end', None)
                    print(f"📅 Retrieved period dates: {current_period_start} → {current_period_end}")
                except Exception as e:
                    print(f"⚠️ Could not retrieve fresh subscription for period dates: {e}")

            return {
                "subscription_id": subscription.id,
                "status": subscription.status,
                "client_secret": client_secret,
                "current_period_start": current_period_start,
                "current_period_end": current_period_end,
                "payment_intent_id": payment_intent_id
            }
            
        except stripe.error.CardError as e:
            raise e
        except stripe.error.InvalidRequestError as e:
            raise e
        except stripe.error.StripeError as e:
            raise e
    
    @staticmethod
    def retrieve_subscription(subscription_id: str) -> Dict[str, Any]:
        """
        Retrieve subscription details from Stripe.

        Returns current_period_start/end as authoritative billing dates.
        Also returns plan_type from subscription metadata so confirm-subscription
        does not have to guess the plan from PaymentIntent metadata (unreliable —
        Stripe does not copy subscription metadata onto the auto-generated PaymentIntent).
        """
        try:
            subscription = stripe.Subscription.retrieve(
                subscription_id,
                expand=["latest_invoice"]
            )

            # Access via dict to bypass SDK attribute caching that can return None
            sub_dict = subscription.to_dict() if hasattr(subscription, 'to_dict') else dict(subscription)
            period_start = sub_dict.get('current_period_start') or getattr(subscription, 'current_period_start', None)
            period_end = sub_dict.get('current_period_end') or getattr(subscription, 'current_period_end', None)

            # plan_type lives in subscription metadata — authoritative source
            # Do NOT read plan_type from PaymentIntent metadata; Stripe does not
            # copy subscription metadata onto auto-generated PaymentIntents.
            metadata = sub_dict.get('metadata') or {}
            plan_type_from_stripe = metadata.get('plan_type')

            return {
                "id": subscription.id,
                "status": subscription.status,
                "current_period_start": period_start,
                "current_period_end": period_end,
                "plan_type": plan_type_from_stripe,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "canceled_at": subscription.canceled_at,
                "items": [{
                    "price_id": item.price.id,
                    "interval": item.price.recurring.interval if item.price.recurring else None
                } for item in subscription["items"].data]
            }
        except stripe.error.StripeError as e:
            raise e
    
    @staticmethod
    def cancel_subscription(subscription_id: str, at_period_end: bool = True) -> Dict[str, Any]:
        """
        Cancel a subscription.
        
        at_period_end=True (default): User keeps access until period end, then cancelled.
        at_period_end=False: Cancels immediately, no refund.
        """
        try:
            if at_period_end:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
            else:
                subscription = stripe.Subscription.delete(subscription_id)
            
            return {
                "id": subscription.id,
                "status": subscription.status,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "canceled_at": subscription.canceled_at
            }
        except stripe.error.StripeError as e:
            raise e
    
    @staticmethod
    def update_subscription_price(
        subscription_id: str,
        new_price_id: str,
        prorate: bool = True
    ) -> Dict[str, Any]:
        """Update subscription to a different plan (upgrade/downgrade)."""
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            subscription = stripe.Subscription.modify(
                subscription_id,
                items=[{
                    'id': subscription['items']['data'][0].id,
                    'price': new_price_id,
                }],
                proration_behavior='always_invoice' if prorate else 'none',
            )
            return {
                "id": subscription.id,
                "status": subscription.status,
                "current_period_end": getattr(subscription, 'current_period_end', None)
            }
        except stripe.error.StripeError as e:
            raise e
    
    @staticmethod
    def get_customer_payment_methods(customer_id: str) -> list:
        """Get all saved payment methods for a customer."""
        try:
            payment_methods = stripe.PaymentMethod.list(customer=customer_id, type="card")
            return [{
                "id": pm.id,
                "brand": pm.card.brand,
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year
            } for pm in payment_methods.data]
        except stripe.error.StripeError as e:
            raise e
    
    @staticmethod
    def create_setup_intent(customer_id: str) -> Dict[str, Any]:
        """Create a SetupIntent for saving a card without an immediate charge."""
        try:
            setup_intent = stripe.SetupIntent.create(
                customer=customer_id,
                payment_method_types=["card"],
            )
            return {
                "client_secret": setup_intent.client_secret,
                "setup_intent_id": setup_intent.id
            }
        except stripe.error.StripeError as e:
            raise Exception(f"Setup intent creation error: {str(e)}")

    @staticmethod
    def detach_payment_method(payment_method_id: str) -> Dict[str, Any]:
        """Detach a payment method from a customer."""
        try:
            payment_method = stripe.PaymentMethod.detach(payment_method_id)
            return {"status": "success", "payment_method_id": payment_method.id}
        except stripe.error.StripeError as e:
            raise Exception(f"Payment method detachment error: {str(e)}")