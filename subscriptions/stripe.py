from fastapi import APIRouter, HTTPException, Depends, Request, Header
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
from decimal import Decimal
import os
import secrets
import stripe

from db.pg_connections import get_db
from db.pg_models import (
    PaymentIntentCreate, PaymentIntentResponse, PaymentVerify,
    SubscriptionResponse, CreateSubscriptionRequest,
    UpdatePaymentMethodRequest, ConfirmSubscriptionRequest, SaveCardRequest
)

from .stripe_service import StripeService
from db.pg_models import User, Subscriptions
from api.routes.login import get_current_user
import json
import logging
import traceback

logger = logging.getLogger(__name__)

from fastapi import BackgroundTasks
from emailing.email_service import email_service
from api.services.notification_service import NotificationService
from db.pg_models import NotificationType
from .beta_service import BetaService

router = APIRouter(prefix="/api/stripe", tags=["stripe"])


# ---------------------------------------------------------------------------
# BILLING FLOW OVERVIEW
# ---------------------------------------------------------------------------
#
# BETA USERS (APP_MODE=beta):
#   1. User registers → grace period set to launch_date + grace_days
#   2. User visits /upgrade → sees "save card" CTA
#   3. User saves card via POST /save-card-beta
#      → Card attached to Stripe customer, stored on user record
#      → NO subscription created yet (billing deferred to launch)
#   4. At launch (APP_MODE=launch):
#      → save-card-beta detects launch mode and immediately creates subscription
#      → OR a migration script calls create_subscription_with_saved_card for all
#         beta users who saved cards but haven't been billed yet
#   5. Subscription created → invoice.payment_succeeded fires → webhook updates DB
#
# NEW USERS AFTER LAUNCH (APP_MODE=launch):
#   1. User registers → grace period set to now + grace_days
#   2. User visits /upgrade → sees plan pricing
#   3. User pays via POST /create-subscription-with-saved-card
#      → Stripe Subscription (sub_xxx) created immediately
#      → If 3DS needed: frontend confirms, then POST /confirm-subscription
#      → DB record created, user.stripe_subscription_id stored
#   4. On renewal: Stripe fires invoice.payment_succeeded → webhook extends end_date
#   5. On failure: Stripe fires invoice.payment_failed → webhook can notify user
#
# IMPORTANT: Never use create-payment-intent for subscriptions.
# PaymentIntents are one-time charges — Stripe will never auto-renew them.
# ---------------------------------------------------------------------------


def get_stripe_price_id(plan_type: str) -> str:
    """Get Price ID for a plan type from environment variables."""
    env_keys = {
        "monthly": "STRIPE_MONTHLY_PRICE_ID",
        "yearly": "STRIPE_YEARLY_PRICE_ID",
        "quarterly": "STRIPE_QUARTERLY_PRICE_ID"
    }
    key = env_keys.get(plan_type)
    if not key:
        return ""
    return os.getenv(key, "").strip()


def generate_tx_ref(prefix: str = "STRIPE") -> str:
    """Generate a unique transaction reference."""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_str = secrets.token_hex(4).upper()
    return f"{prefix}-{timestamp}-{random_str}"


def extract_user_id(current_user) -> int:
    """
    Centralised helper to extract user_id from whatever shape get_current_user returns.
    Eliminates the repeated if/elif blocks scattered across every endpoint.
    """
    if isinstance(current_user, dict):
        if "user" in current_user:
            user_data = current_user["user"]
            if isinstance(user_data, dict):
                uid = user_data.get("id") or user_data.get("user_id")
            elif hasattr(user_data, 'id'):
                uid = user_data.id
            else:
                uid = user_data
        else:
            uid = current_user.get("id") or current_user.get("user_id") or current_user.get("sub")
        if uid is None:
            raise HTTPException(status_code=500, detail="Could not extract user_id from token")
        return int(uid)
    return int(current_user.id)


def get_subscription_dates_from_stripe(subscription_result: dict, plan_type: str):
    """
    Get subscription start/end dates from Stripe's response.
    
    ALWAYS prefer Stripe's current_period_start/end over local timedelta calculation.
    Stripe owns the billing cycle — local timedelta drifts over time and disagrees
    with what Stripe will actually charge on the next cycle.
    
    Falls back to timedelta only if Stripe didn't return period data (e.g. incomplete
    subscription before first payment).
    """
    period_start = subscription_result.get("current_period_start")
    period_end = subscription_result.get("current_period_end")
    
    # Try latest_invoice if dates are missing (common for incomplete subscriptions)
    if not (period_start and period_end):
        latest_invoice = subscription_result.get("latest_invoice")
        if isinstance(latest_invoice, dict):
            lines = latest_invoice.get("lines", {}).get("data", [])
            for line in lines:
                if line.get("period"):
                    period_start = period_start or line["period"].get("start")
                    period_end = period_end or line["period"].get("end")
                    if period_start and period_end:
                        logger.info(f"📅 Extracted period dates from latest_invoice: {period_start} -> {period_end}")
                        break

    if period_start and period_end:
        try:
            start_date = datetime.fromtimestamp(int(period_start))
            end_date = datetime.fromtimestamp(int(period_end))
            logger.info(f"📅 Using Stripe authoritative dates: {start_date} -> {end_date}")
            return start_date, end_date
        except (ValueError, TypeError, OverflowError) as e:
            logger.warning(f"⚠️ Error parsing Stripe timestamps: {e}. Falling back.")
    
    # Fallback for incomplete subscriptions awaiting 3DS
    status = subscription_result.get("status", "unknown")
    if status == "incomplete":
        logger.info(f"ℹ️ Subscription {subscription_result.get('id')} is incomplete; using estimated dates for {plan_type}")
    else:
        logger.warning(f"⚠️ Stripe period data missing for {plan_type} (status: {status}), falling back to timedelta")
        
    start = datetime.utcnow()
    delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
    return start, start + timedelta(days=delta_map.get(plan_type, 30))


@router.get("/config")
async def get_stripe_config():
    """Get Stripe publishable key for frontend."""
    publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY")
    if not publishable_key:
        raise HTTPException(status_code=500, detail="Stripe configuration not found")
    return {"publishableKey": publishable_key}


@router.get("/history", response_model=list[SubscriptionResponse])
async def get_subscription_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user's subscription/payment history."""
    try:
        user_id = extract_user_id(current_user)
        subscriptions = db.query(Subscriptions).filter(
            Subscriptions.user_id == user_id
        ).order_by(Subscriptions.created_at.desc()).all()
        return subscriptions
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch subscription history")


# ---------------------------------------------------------------------------
# LEGACY ENDPOINT — kept for backwards compatibility only
# New signups should use /create-subscription-with-saved-card
# ---------------------------------------------------------------------------

@router.post("/create-payment-intent", response_model=PaymentIntentResponse)
async def create_payment_intent(
    payment_data: PaymentIntentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    LEGACY: Create a one-time Stripe Payment Intent.
    
    ⚠️  DO NOT use this for subscriptions. This creates a single charge only.
    Stripe will never auto-renew a PaymentIntent — that's why test payments
    didn't appear as subscriptions in the Stripe dashboard.
    
    This endpoint is retained so existing clients don't break, but all new
    subscription flows must use /create-subscription-with-saved-card.
    """
    try:
        user_id = extract_user_id(current_user)
        
        if int(payment_data.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        tx_ref = generate_tx_ref("STRIPE")
        intent = StripeService.create_payment_intent(
            amount=payment_data.amount,
            currency="usd",
            customer_email=payment_data.email,
            metadata={
                "user_id": str(payment_data.user_id),
                "plan_type": payment_data.plan_type,
                "customer_name": payment_data.name,
                "tx_ref": tx_ref,
                "legacy_payment_intent": "true"  # Flag for webhook identification
            }
        )
        return intent
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify-payment", response_model=SubscriptionResponse)
async def verify_payment(
    payment_verify: PaymentVerify,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    LEGACY: Verify a PaymentIntent and create a local subscription record.
    
    ⚠️  This works for the initial payment but the subscription will NOT
    auto-renew because there is no Stripe Subscription (sub_xxx) object.
    Users paid through this flow need to be migrated — see comments in
    the invoice.payment_succeeded webhook handler.
    """
    try:
        user_id = extract_user_id(current_user)
        
        if int(payment_verify.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        verification = StripeService.verify_payment(payment_verify.payment_intent_id)
        
        if verification["status"] != "succeeded":
            NotificationService.create_notification(
                db=db,
                user_id=payment_verify.user_id,
                type=NotificationType.PAYMENT_FAILED.value,
                title="Payment Failed",
                message=f"Your payment was not successful (Status: {verification['status']}).",
                link="/dashboard/upgrade"
            )
            raise HTTPException(status_code=400, detail=f"Payment not successful. Status: {verification['status']}")
        
        existing_sub = db.query(Subscriptions).filter(
            Subscriptions.transaction_id == payment_verify.payment_intent_id
        ).first()
        if existing_sub:
            return existing_sub
        
        metadata = verification.get("metadata", {})
        plan_type = metadata.get("plan_type", "monthly")
        tx_ref = metadata.get("tx_ref", generate_tx_ref("STRIPE"))
        
        # Use timedelta here since there's no Stripe Subscription to pull dates from.
        # These users will not auto-renew — they need to be migrated to proper subscriptions.
        start_date = datetime.utcnow()
        delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
        end_date = start_date + timedelta(days=delta_map.get(plan_type, 30))
        
        subscription = Subscriptions(
            user_id=payment_verify.user_id,
            subscription_plan=plan_type,
            transaction_id=payment_verify.payment_intent_id,
            tx_ref=tx_ref,
            amount=Decimal(str(verification.get("amount", 0))),
            currency=verification.get("currency", "USD").upper(),
            status="completed",
            subscription_status="active",
            payment_provider="stripe",
            start_date=start_date,
            end_date=end_date
        )
        db.add(subscription)
        db.flush()
        
        user = db.query(User).filter(User.id == payment_verify.user_id).first()
        if user:
            if hasattr(user, 'subscription_status'):
                user.subscription_status = "active"
            if hasattr(user, 'subscription_plan'):
                user.subscription_plan = plan_type
            if hasattr(user, 'subscription_expires_at'):
                user.subscription_expires_at = end_date
        
        from subscriptions.commission_service import CommissionService
        CommissionService.calculate_commission(subscription=subscription, db=db)
        
        db.commit()
        db.refresh(subscription)
        
        if user:
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name,
                float(verification.get("amount", 0)),
                plan_type, end_date.strftime("%B %d, %Y")
            )
        
        NotificationService.create_notification(
            db=db,
            user_id=payment_verify.user_id,
            type=NotificationType.PAYMENT_SUCCESS.value,
            title="Subscription Active!",
            message=f"Your {plan_type} subscription is now active.",
            link="/dashboard"
        )
        return subscription
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# BETA CARD SAVE
# ---------------------------------------------------------------------------

@router.post("/save-card-beta")
async def save_card_for_beta(
    request: SaveCardRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Save a payment card during the beta period OR immediately bill in launch mode.
    
    BETA MODE (APP_MODE=beta):
        - Attaches payment method to Stripe customer
        - Stores card metadata on user record (last4, brand, etc.)
        - Does NOT create a Stripe Subscription yet
        - User is marked as beta user with grace period tied to launch date
        - At launch, all beta users with saved cards are billed via a migration
          or the next call in launch mode triggers billing automatically
    
    LAUNCH MODE (APP_MODE=launch):
        - Same card save as above
        - Additionally creates a Stripe Subscription immediately
        - Uses the price_id matching user.subscription_plan (defaults to monthly)
        - Subscription auto-renews from this point forward
        - If 3DS required → returns requires_action with client_secret
    """
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        from subscriptions.beta_service import BetaService
        
        app_mode = BetaService.get_app_mode()
        
        if not BetaService.is_beta_mode() and not BetaService.is_in_grace_period(user) and app_mode != "launch":
            raise HTTPException(
                status_code=400,
                detail="Grace period has ended. Please subscribe to continue using Lavoo."
            )
        
        # Get or create Stripe customer
        customer_id = StripeService.get_or_create_customer(
            user_id=user_id,
            email=user.email,
            name=user.name,
            stripe_customer_id=getattr(user, 'stripe_customer_id', None)
        )
        
        if not getattr(user, 'stripe_customer_id', None):
            user.stripe_customer_id = customer_id
        
        # Attach payment method and set as default for future subscription billing
        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=customer_id,
            set_as_default=True
        )
        
        # Store safe card metadata (PCI compliant — no full card numbers)
        payment_method = stripe.PaymentMethod.retrieve(request.payment_method_id)
        user.stripe_payment_method_id = request.payment_method_id
        user.card_last4 = payment_method.card.last4
        user.card_brand = payment_method.card.brand
        user.card_exp_month = payment_method.card.exp_month
        user.card_exp_year = payment_method.card.exp_year
        user.card_saved_at = datetime.utcnow()

        # Persist the plan the user selected so the cron script and launch billing
        # use the correct price. Without this, user.subscription_plan stays None
        # and both flows fall back to "monthly" regardless of user selection.
        requested_plan = getattr(request, 'plan_type', None) or getattr(user, 'subscription_plan', None) or "monthly"
        if hasattr(user, 'subscription_plan'):
            user.subscription_plan = requested_plan

        if BetaService.is_beta_mode():
            BetaService.mark_as_beta_user(user, db)

        # LAUNCH MODE: Immediately create a Stripe Subscription
        # This covers two cases:
        #   (a) User saves card after launch — bill immediately
        #   (b) Beta user (already had card) whose save-card is re-triggered at launch
        if app_mode == "launch" and not (hasattr(user, 'stripe_subscription_id') and user.stripe_subscription_id):
            logger.info(f"🚀 [LAUNCH] Triggering immediate billing for user {user.id} ({user.email})")
            try:
                plan_type = requested_plan
                price_id = get_stripe_price_id(plan_type)
                
                if not price_id:
                    logger.error(f"No price_id found for plan {plan_type}")
                    raise HTTPException(status_code=400, detail=f"Price not configured for plan: {plan_type}")
                
                sub_result = StripeService.create_subscription_with_saved_card(
                    customer_id=customer_id,
                    price_id=price_id,
                    payment_method_id=request.payment_method_id,
                    metadata={
                        "user_id": str(user.id),
                        "plan_type": plan_type,
                        "source": "beta_launch_billing"
                    },
                    off_session=False  # User IS present — allow 3DS modal via frontend
                )
                
                if sub_result.get("status") == "incomplete":
                    # 3D Secure required — commit card save, return action required
                    db.commit()
                    return {
                        "status": "requires_action",
                        "subscription_id": sub_result["subscription_id"],
                        "payment_intent_id": sub_result.get("payment_intent_id"),
                        "client_secret": sub_result.get("client_secret"),
                        "message": "Additional authentication required"
                    }
                
                if sub_result.get("status") in ["active", "trialing"]:
                    # Use Stripe's period dates — not local timedelta
                    start_date, end_date = get_subscription_dates_from_stripe(sub_result, plan_type)
                    
                    if hasattr(user, 'stripe_subscription_id'):
                        user.stripe_subscription_id = sub_result["subscription_id"]
                    
                    # Get pricing from settings
                    from api.routes.control.settings import get_settings
                    settings = get_settings(db=db, current_user=user)
                    price_map = {
                        "monthly": settings.monthly_price,
                        "quarterly": settings.quarterly_price,
                        "yearly": settings.yearly_price
                    }
                    amount = price_map.get(plan_type, 29.95)
                    
                    subscription = Subscriptions(
                        user_id=user.id,
                        subscription_plan=plan_type,
                        transaction_id=sub_result["subscription_id"],
                        tx_ref=generate_tx_ref("LAUNCH"),
                        amount=Decimal(str(amount)),
                        currency="USD",
                        status="completed",
                        subscription_status="active",
                        payment_provider="stripe",
                        start_date=start_date,
                        end_date=end_date
                    )
                    db.add(subscription)
                    db.flush()
                    
                    user.subscription_status = "active"
                    user.subscription_expires_at = end_date
                    if hasattr(user, 'subscription_plan'):
                        user.subscription_plan = plan_type
                    
                    from subscriptions.commission_service import CommissionService
                    CommissionService.calculate_commission(subscription=subscription, db=db)
                    
                    background_tasks.add_task(
                        email_service.send_payment_success_email,
                        user.email, user.name, float(amount),
                        plan_type, end_date.strftime("%B %d, %Y")
                    )
                    
                    NotificationService.create_notification(
                        db=db, user_id=user.id,
                        type="subscription_active",
                        title="Subscription Activated",
                        message="Your subscription is now active. Welcome to Lavoo!",
                        link="/dashboard/upgrade"
                    )
                    logger.info(f"✅ Launch billing successful for user {user.id}")
                    
            except HTTPException:
                raise
            except Exception as auto_err:
                logger.error(f"❌ Launch billing failed for user {user.id}: {str(auto_err)}")
                logger.error(traceback.format_exc())
                # Don't re-raise — card is saved even if billing fails
                # The billing attempt will be retried via migration script
        
        db.commit()
        
        # Send card-saved confirmation email (beta or regular)
        background_tasks.add_task(
            email_service.send_beta_card_saved_email,
            user.email, user.name, user.card_last4, user.card_brand,
            BetaService.get_grace_period_days()
        )
        
        NotificationService.create_notification(
            db=db,
            user_id=user.id,
            type="card_saved",
            title="✅ Card Saved Successfully",
            message="Congratulations on becoming a part of the Lavoo Community! Your card is securely saved." if BetaService.is_beta_mode() else "Congratulations on becoming a part of the Lavoo Community! Your subscription is now active.",
            link="/dashboard"
        )
        
        return {
            "status": "success",
            "message": "Congratulations on becoming a part of the Lavoo Community",
            "card_info": {
                "last4": user.card_last4,
                "brand": user.card_brand,
                "exp_month": user.card_exp_month,
                "exp_year": user.card_exp_year
            },
            "grace_period_days": BetaService.get_grace_period_days(),
            "grace_period_ends": user.grace_period_ends_at.isoformat() if user.grace_period_ends_at else None
        }
        
    except HTTPException:
        db.rollback()
        raise
    except stripe.error.CardError as e:
        db.rollback()
        error_msg = str(e.user_message) if hasattr(e, 'user_message') else str(e)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/beta/status")
async def get_beta_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's beta/subscription status for dashboard display."""
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        from subscriptions.beta_service import BetaService
        status = BetaService.get_user_status(user)
        
        if status.get("show_card_info") and user.card_last4:
            status["card_info"] = {
                "last4": user.card_last4,
                "brand": user.card_brand,
                "exp_month": user.card_exp_month,
                "exp_year": user.card_exp_year
            }
        
        status["is_beta_mode"] = BetaService.is_beta_mode()
        status["is_in_grace_period"] = BetaService.is_in_grace_period(user)
        return status
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# WEBHOOK
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events.
    
    Critical events handled:
    
    invoice.payment_succeeded
        Fired on every successful subscription renewal (and the first payment).
        This is how we extend subscription access after each billing cycle.
        Uses Stripe's current_period_end as the authoritative end date.
    
    invoice.payment_failed
        Fired when Stripe's Smart Retries are exhausted. Notify user to update card.
    
    customer.subscription.deleted
        Fired when a subscription is fully cancelled (after period end or immediately).
        Revokes user access.
    
    customer.subscription.updated
        Fired when a subscription changes status (e.g. past_due, active after retry).
    
    payment_intent.succeeded (LEGACY)
        Only processes PaymentIntents tagged with legacy_payment_intent=true in metadata.
        Handles old-flow users who paid before the subscription migration.
    """
    try:
        payload = await request.body()
        
        is_production = os.getenv("ENVIRONMENT", "development") == "production"
        if not stripe_signature and not is_production:
            logger.warning("⚠️ Webhook: No signature — manual test mode")
            try:
                event_data = json.loads(payload)
                event = stripe.Event.construct_from(event_data, stripe.api_key)
            except Exception as e:
                raise HTTPException(status_code=400, detail="Invalid JSON payload")
        else:
            event = StripeService.verify_webhook_signature(payload, stripe_signature)
        
        logger.info(f"📨 Webhook received: {event.type}")
        
        # ------------------------------------------------------------------
        # invoice.payment_succeeded
        # The primary renewal handler. Fired for every successful invoice
        # payment — both the first subscription payment and all renewals.
        # ------------------------------------------------------------------
        if event.type == "invoice.payment_succeeded":
            invoice = event.data.object
            subscription_id = invoice.get("subscription")
            
            if not subscription_id:
                # One-time invoice, not a subscription invoice — skip
                return {"status": "success"}
            
            logger.info(f"🔄 Renewal/payment for subscription {subscription_id}")
            
            # Retrieve the full subscription to get authoritative period dates
            stripe_sub = stripe.Subscription.retrieve(subscription_id)
            start_date = datetime.fromtimestamp(stripe_sub.current_period_start)
            end_date = datetime.fromtimestamp(stripe_sub.current_period_end)
            
            # Find the user by their stored stripe_subscription_id
            user = db.query(User).filter(
                User.stripe_subscription_id == subscription_id
            ).first()
            
            if not user:
                # Fallback: try to find user via invoice metadata
                metadata = getattr(invoice, 'metadata', {}) or {}
                user_id_meta = metadata.get("user_id")
                if user_id_meta:
                    user = db.query(User).filter(User.id == int(user_id_meta)).first()
            
            if not user:
                logger.warning(f"⚠️ No user found for subscription {subscription_id}")
                return {"status": "success"}
            
            # Idempotency: skip if this invoice was already processed
            existing = db.query(Subscriptions).filter(
                Subscriptions.transaction_id == invoice.payment_intent
            ).first()
            if existing:
                logger.info(f"ℹ️ Invoice {invoice.payment_intent} already recorded, skipping")
                return {"status": "success"}
            
            # Extend subscription access using Stripe's period end (not timedelta)
            user.subscription_status = "active"
            user.subscription_expires_at = end_date
            if hasattr(user, 'stripe_subscription_id') and not user.stripe_subscription_id:
                user.stripe_subscription_id = subscription_id
            
            # Create a historical billing record for this period
            new_sub = Subscriptions(
                user_id=user.id,
                subscription_plan=user.subscription_plan or "monthly",
                transaction_id=invoice.payment_intent,
                tx_ref=f"RENEW-{user.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                amount=Decimal(str(invoice.amount_paid / 100)),
                currency=invoice.currency.upper(),
                status="completed",
                subscription_status="active",
                payment_provider="stripe",
                start_date=start_date,
                end_date=end_date
            )
            db.add(new_sub)
            db.flush()
            
            from subscriptions.commission_service import CommissionService
            CommissionService.calculate_commission(subscription=new_sub, db=db)
            
            db.commit()
            logger.info(f"✅ Renewal recorded for user {user.id}, expires {end_date}")
            
            NotificationService.create_notification(
                db=db,
                user_id=user.id,
                type="subscription_renewed",
                title="✅ Subscription Renewed",
                message=f"Your subscription has been successfully renewed. Your access is secured until {end_date.strftime('%B %d, %Y')}.",
                link="/dashboard/upgrade"
            )
            db.commit()
        
        # ------------------------------------------------------------------
        # invoice.payment_failed
        # Stripe has retried automatically and all retries are exhausted.
        # Notify the user to update their payment method.
        # ------------------------------------------------------------------
        elif event.type == "invoice.payment_failed":
            invoice = event.data.object
            subscription_id = invoice.subscription
            
            if subscription_id:
                user = db.query(User).filter(
                    User.stripe_subscription_id == subscription_id
                ).first()
                
                if user:
                    NotificationService.create_notification(
                        db=db,
                        user_id=user.id,
                        type=NotificationType.PAYMENT_FAILED.value,
                        title="Payment Failed",
                        message="Your subscription payment failed. Please update your payment method to keep access.",
                        link="/dashboard/upgrade"
                    )
                    db.commit()
                    logger.warning(f"⚠️ Payment failed for user {user.id}, subscription {subscription_id}")
        
        # ------------------------------------------------------------------
        # customer.subscription.deleted
        # Subscription fully cancelled — revoke access.
        # ------------------------------------------------------------------
        elif event.type == "customer.subscription.deleted":
            stripe_sub = event.data.object
            subscription_id = stripe_sub.id
            
            user = db.query(User).filter(
                User.stripe_subscription_id == subscription_id
            ).first()
            
            if user:
                user.subscription_status = "cancelled"
                if hasattr(user, 'stripe_subscription_id'):
                    user.stripe_subscription_id = None
                
                sub_record = db.query(Subscriptions).filter(
                    Subscriptions.user_id == user.id,
                    Subscriptions.subscription_status == "active"
                ).first()
                if sub_record:
                    sub_record.subscription_status = "cancelled"
                    sub_record.status = "cancelled"
                
                NotificationService.create_notification(
                    db=db,
                    user_id=user.id,
                    type="subscription_cancelled",
                    title="Subscription Cancelled",
                    message="Your subscription has been cancelled. You can resubscribe at any time.",
                    link="/dashboard/upgrade"
                )
                db.commit()
                logger.info(f"✅ Subscription cancelled for user {user.id}")
        
        # ------------------------------------------------------------------
        # customer.subscription.updated
        # Status change: active → past_due, past_due → active (after retry), etc.
        # ------------------------------------------------------------------
        elif event.type == "customer.subscription.updated":
            stripe_sub = event.data.object
            subscription_id = stripe_sub.id
            new_status = stripe_sub.status
            
            user = db.query(User).filter(
                User.stripe_subscription_id == subscription_id
            ).first()
            
            if user:
                status_map = {
                    "active": "active",
                    "past_due": "past_due",
                    "unpaid": "unpaid",
                    "canceled": "cancelled",
                    "trialing": "active"
                }
                mapped = status_map.get(new_status)
                if mapped and hasattr(user, 'subscription_status'):
                    user.subscription_status = mapped
                db.commit()
                logger.info(f"✅ Subscription status updated to '{new_status}' for user {user.id}")
        
        # ------------------------------------------------------------------
        # payment_intent.succeeded (LEGACY ONLY)
        # Only handle PaymentIntents created by the old create-payment-intent
        # endpoint (flagged with legacy_payment_intent=true in metadata).
        # New subscription flows use invoice.payment_succeeded instead.
        # ------------------------------------------------------------------
        elif event.type == "payment_intent.succeeded":
            payment_intent = event.data.object
            metadata = payment_intent.metadata or {}
            
            # Skip if this PI belongs to a subscription invoice (Stripe creates PIs for those too)
            if not metadata.get("legacy_payment_intent"):
                return {"status": "success"}
            
            existing = db.query(Subscriptions).filter(
                Subscriptions.transaction_id == payment_intent.id
            ).first()
            
            if existing:
                if existing.status != "completed":
                    existing.status = "completed"
                    db.commit()
                return {"status": "success"}
            
            user_id = int(metadata.get("user_id", 0))
            plan_type = metadata.get("plan_type", "monthly")
            tx_ref = metadata.get("tx_ref", generate_tx_ref("STRIPE"))
            
            if user_id:
                start = datetime.utcnow()
                delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
                end = start + timedelta(days=delta_map.get(plan_type, 30))
                
                subscription = Subscriptions(
                    user_id=user_id,
                    subscription_plan=plan_type,
                    transaction_id=payment_intent.id,
                    tx_ref=tx_ref,
                    amount=Decimal(str(payment_intent.amount / 100)),
                    currency=payment_intent.currency.upper(),
                    status="completed",
                    subscription_status="active",
                    payment_provider="stripe",
                    start_date=start,
                    end_date=end
                )
                db.add(subscription)
                db.flush()
                
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    if hasattr(user, 'subscription_status'):
                        user.subscription_status = "active"
                    if hasattr(user, 'subscription_plan'):
                        user.subscription_plan = plan_type
                    if hasattr(user, 'subscription_expires_at'):
                        user.subscription_expires_at = end
                
                from subscriptions.commission_service import CommissionService
                CommissionService.calculate_commission(subscription=subscription, db=db)
                db.commit()
                logger.info(f"✅ Legacy payment processed for user {user_id}")
        
        # Payout events (unchanged)
        elif event.type == "payout.paid":
            handle_payout_paid(event, db)
        elif event.type in ("payout.failed", "payout.canceled"):
            handle_payout_failed(event, db)
        
        return {"status": "success"}
        
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"❌ Webhook signature verification failed: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except Exception as e:
        event_type = event.type if 'event' in locals() else 'unknown'
        logger.error(f"❌ Webhook error [{event_type}]: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


def handle_payout_paid(event: dict, db: Session):
    stripe_payout = event.data.object
    metadata = stripe_payout.get("metadata", {})
    internal_payout_id = metadata.get("stripe_connect_payout_id")
    if not internal_payout_id:
        return
    from db.pg_models import Payout
    from subscriptions.payout_service import PayoutService
    payout = db.query(Payout).get(internal_payout_id)
    if not payout or payout.status == "completed":
        return
    from fastapi import BackgroundTasks
    PayoutService.complete_stripe_payout(payout.id, BackgroundTasks(), "paid", db)


def handle_payout_failed(event: dict, db: Session):
    stripe_payout = event.data.object
    metadata = stripe_payout.get("metadata", {})
    internal_payout_id = metadata.get("stripe_connect_payout_id")
    if not internal_payout_id:
        return
    from subscriptions.payout_service import PayoutService
    failure_reason = stripe_payout.get("failure_message") or "Stripe payout failed"
    PayoutService.reverse_payout(internal_payout_id, failure_reason, db)


# ---------------------------------------------------------------------------
# CREATE SUBSCRIPTION WITH SAVED CARD (primary post-launch flow)
# ---------------------------------------------------------------------------

@router.post("/create-subscription-with-saved-card")
async def create_subscription_with_saved_card(
    request: CreateSubscriptionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a Stripe Subscription using an existing payment method.
    
    This is the PRIMARY subscription endpoint for post-launch users.
    Unlike the legacy /create-payment-intent, this creates a real Stripe
    Subscription (sub_xxx) that:
    - Appears in Stripe Dashboard → Subscriptions
    - Auto-renews at each billing cycle
    - Fires invoice.payment_succeeded on every renewal
    - Supports Test Clock simulation for renewal testing
    
    Flow:
    1. Get or create Stripe Customer
    2. Attach payment method as customer default
    3. Create subscription via stripe.Subscription.create()
    4. If card requires 3DS: return requires_action + client_secret
    5. Frontend calls stripe.confirmCardPayment(client_secret)
    6. Frontend calls POST /confirm-subscription to finalise DB record
    7. If no 3DS: subscription is active immediately, DB record created here
    """
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        price_id = get_stripe_price_id(request.plan_type)
        if not price_id:
            raise HTTPException(
                status_code=400,
                detail=f"Price ID not configured for plan: {request.plan_type}. "
                       f"Set STRIPE_{request.plan_type.upper()}_PRICE_ID in environment."
            )
        
        customer_id = StripeService.get_or_create_customer(
            user_id=user_id,
            email=user.email,
            name=user.name,
            stripe_customer_id=getattr(user, 'stripe_customer_id', None)
        )
        
        if not getattr(user, 'stripe_customer_id', None) and hasattr(user, 'stripe_customer_id'):
            user.stripe_customer_id = customer_id
            db.commit()
        
        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=customer_id,
            set_as_default=True
        )
        
        # Check for existing active subscription — upgrade/downgrade instead of creating new
        existing_stripe_sub_id = getattr(user, 'stripe_subscription_id', None)
        if existing_stripe_sub_id:
            try:
                existing_sub = StripeService.retrieve_subscription(existing_stripe_sub_id)
                if existing_sub["status"] == "active":
                    updated_sub = StripeService.update_subscription_price(
                        subscription_id=existing_stripe_sub_id,
                        new_price_id=price_id,
                        prorate=True
                    )
                    sub_record = db.query(Subscriptions).filter(
                        Subscriptions.user_id == user_id,
                        Subscriptions.subscription_status == "active"
                    ).first()
                    if sub_record:
                        sub_record.subscription_plan = request.plan_type
                        period_end = updated_sub.get("current_period_end")
                        if period_end:
                            sub_record.end_date = datetime.fromtimestamp(period_end)
                        db.commit()
                    return {
                        "status": "active",
                        "subscription_id": updated_sub["id"],
                        "message": "Subscription updated successfully"
                    }
            except Exception:
                pass  # Subscription invalid/expired — create a new one
        
        tx_ref = generate_tx_ref("STRIPE-SUB")
        
        subscription_result = StripeService.create_subscription_with_saved_card(
            customer_id=customer_id,
            price_id=price_id,
            payment_method_id=request.payment_method_id,
            metadata={
                "user_id": str(user_id),
                "plan_type": request.plan_type,
                "tx_ref": tx_ref
            }
        )
        
        if subscription_result["status"] == "active":
            # Use Stripe's period dates — not local timedelta
            start_date, end_date = get_subscription_dates_from_stripe(subscription_result, request.plan_type)
            
            # Idempotency
            if db.query(Subscriptions).filter(
                Subscriptions.transaction_id == subscription_result["subscription_id"]
            ).first():
                return {"status": "active", "subscription_id": subscription_result["subscription_id"]}
            
            from api.routes.control.settings import get_settings
            settings = get_settings(db=db, current_user=user)
            price_map = {
                "monthly": settings.monthly_price,
                "quarterly": settings.quarterly_price,
                "yearly": settings.yearly_price
            }
            amount = price_map.get(request.plan_type, 29.95)
            
            subscription = Subscriptions(
                user_id=user_id,
                subscription_plan=request.plan_type,
                transaction_id=subscription_result["subscription_id"],
                tx_ref=tx_ref,
                amount=Decimal(str(amount)),
                currency="USD",
                status="completed",
                subscription_status="active",
                payment_provider="stripe",
                start_date=start_date,
                end_date=end_date
            )
            db.add(subscription)
            db.flush()
            
            if hasattr(user, 'subscription_status'):
                user.subscription_status = "active"
            if hasattr(user, 'subscription_plan'):
                user.subscription_plan = request.plan_type
            if hasattr(user, 'subscription_expires_at'):
                user.subscription_expires_at = end_date
            if hasattr(user, 'stripe_subscription_id'):
                user.stripe_subscription_id = subscription_result["subscription_id"]
            
            # Save card details to user record
            try:
                pm = stripe.PaymentMethod.retrieve(request.payment_method_id)
                user.stripe_payment_method_id = request.payment_method_id
                user.card_last4 = pm.card.last4
                user.card_brand = pm.card.brand
                user.card_exp_month = pm.card.exp_month
                user.card_exp_year = pm.card.exp_year
                user.card_saved_at = datetime.utcnow()
            except Exception as card_err:
                logger.warning(f"⚠️ Could not save card details: {str(card_err)}")
            
            from subscriptions.commission_service import CommissionService
            CommissionService.calculate_commission(subscription=subscription, db=db)
            
            db.commit()
            db.refresh(subscription)
            
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                request.plan_type, end_date.strftime("%B %d, %Y")
            )
            
            return {
                "status": "active",
                "subscription_id": subscription_result["subscription_id"],
                "subscription": subscription
            }
        
        elif subscription_result["status"] == "incomplete":
            if not subscription_result.get("client_secret"):
                raise HTTPException(
                    status_code=500,
                    detail="Subscription requires authentication but client_secret is missing"
                )
            return {
                "status": "requires_action",
                "subscription_id": subscription_result["subscription_id"],
                "payment_intent_id": subscription_result.get("payment_intent_id"),
                "client_secret": subscription_result.get("client_secret"),
                "message": "Additional authentication required"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Subscription creation failed with status: {subscription_result['status']}"
            )
            
    except HTTPException:
        db.rollback()
        raise
    except stripe.error.StripeError as e:
        db.rollback()
        NotificationService.create_notification(
            db=db, user_id=user_id,
            type=NotificationType.PAYMENT_FAILED.value,
            title="Payment Failed",
            message=f"Your subscription payment failed: {str(e)}",
            link="/dashboard/upgrade"
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/confirm-subscription")
async def confirm_subscription(
    request: ConfirmSubscriptionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Finalise a subscription after 3D Secure authentication.
    
    Called by the frontend after stripe.confirmCardPayment() succeeds.
    Creates or updates the DB record for the now-active subscription.
    """
    try:
        user_id = extract_user_id(current_user)
        
        verification = StripeService.verify_payment(request.payment_intent_id)
        if verification["status"] != "succeeded":
            raise HTTPException(
                status_code=400,
                detail=f"Payment not successful. Status: {verification['status']}"
            )
        
        subscription_details = StripeService.retrieve_subscription(request.subscription_id)
        
        if subscription_details["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Subscription not active. Status: {subscription_details['status']}"
            )
        
        # Read plan_type from the Subscription metadata — NOT the PaymentIntent metadata.
        # Stripe does NOT copy subscription metadata onto the auto-generated PaymentIntent,
        # so reading from verification.metadata always returns None and falls back to
        # "monthly" regardless of what the user actually chose.
        # retrieve_subscription() now returns plan_type from subscription.metadata directly.
        plan_type = (
            subscription_details.get("plan_type")          # from subscription metadata ✅
            or verification.get("metadata", {}).get("plan_type")  # PI metadata fallback
            or (user.subscription_plan if db.query(User).filter(User.id == user_id).first() else None)
            or "monthly"                                    # last resort default
        )

        # tx_ref: try subscription metadata first, then PI metadata
        sub_metadata = {}
        tx_ref = sub_metadata.get("tx_ref") or verification.get("metadata", {}).get("tx_ref") or generate_tx_ref("STRIPE-SUB")

        logger.info(f"confirm-subscription: plan_type='{plan_type}' for sub {request.subscription_id}")

        # Use Stripe's period dates
        start_date, end_date = get_subscription_dates_from_stripe(subscription_details, plan_type)
        
        from api.routes.control.settings import get_settings
        user = db.query(User).filter(User.id == user_id).first()
        settings = get_settings(db=db, current_user=user)
        price_map = {
            "monthly": settings.monthly_price,
            "quarterly": settings.quarterly_price,
            "yearly": settings.yearly_price
        }
        amount = price_map.get(plan_type, 29.95)
        
        # Update existing record if one was created during the incomplete flow
        existing = db.query(Subscriptions).filter(
            Subscriptions.transaction_id == request.subscription_id
        ).first()
        
        if existing:
            existing.subscription_status = "active"
            existing.status = "completed"
            existing.end_date = end_date
            existing.start_date = start_date
            subscription = existing
        else:
            subscription = Subscriptions(
                user_id=user_id,
                subscription_plan=plan_type,
                transaction_id=request.subscription_id,
                tx_ref=tx_ref,
                amount=Decimal(str(amount)),
                currency="USD",
                status="completed",
                subscription_status="active",
                payment_provider="stripe",
                start_date=start_date,
                end_date=end_date
            )
            db.add(subscription)
        
        db.flush()
        
        if user:
            if hasattr(user, 'subscription_status'):
                user.subscription_status = "active"
            if hasattr(user, 'subscription_plan'):
                user.subscription_plan = plan_type
            if hasattr(user, 'subscription_expires_at'):
                user.subscription_expires_at = end_date
            if hasattr(user, 'stripe_subscription_id'):
                user.stripe_subscription_id = request.subscription_id
            
            # Save card details from the payment intent if not already stored
            try:
                pm_id = verification.get("payment_method")
                if pm_id and not getattr(user, 'stripe_payment_method_id', None):
                    pm = stripe.PaymentMethod.retrieve(pm_id)
                    user.stripe_payment_method_id = pm_id
                    user.card_last4 = pm.card.last4
                    user.card_brand = pm.card.brand
                    user.card_exp_month = pm.card.exp_month
                    user.card_exp_year = pm.card.exp_year
                    user.card_saved_at = datetime.utcnow()
            except Exception as card_err:
                logger.warning(f"⚠️ Could not save card details: {str(card_err)}")
        
        if not existing:
            from subscriptions.commission_service import CommissionService
            CommissionService.calculate_commission(subscription=subscription, db=db)
        
        db.commit()
        db.refresh(subscription)
        
        if user:
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                plan_type, end_date.strftime("%B %d, %Y")
            )
        
        NotificationService.create_notification(
            db=db,
            user_id=user_id,
            type="subscription_active",
            title="✅ Subscription Activated",
            message=f"Your subscription is now active! Thank you for joining Lavoo. Your access is valid until {end_date.strftime('%B %d, %Y')}.",
            link="/dashboard"
        )
        
        return {"status": "success", "subscription": subscription}
        
    except HTTPException:
        db.rollback()
        raise
    except stripe.error.StripeError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cancel-subscription")
async def cancel_subscription_endpoint(
    at_period_end: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancel user's subscription. Defaults to cancelling at period end."""
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not getattr(user, 'stripe_subscription_id', None):
            raise HTTPException(status_code=404, detail="No active subscription found")
        
        result = StripeService.cancel_subscription(
            subscription_id=user.stripe_subscription_id,
            at_period_end=at_period_end
        )
        
        sub_record = db.query(Subscriptions).filter(
            Subscriptions.user_id == user_id,
            Subscriptions.subscription_status == "active"
        ).first()
        if sub_record:
            sub_record.subscription_status = "canceling" if at_period_end else "cancelled"
            if not at_period_end:
                sub_record.status = "cancelled"
        
        if hasattr(user, 'subscription_status'):
            user.subscription_status = "canceling" if at_period_end else "cancelled"
        
        db.commit()
        return {
            "status": "success",
            "message": "Subscription cancelled" + (" at period end" if at_period_end else " immediately"),
            "cancel_at_period_end": result["cancel_at_period_end"]
        }
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/update-payment-method")
async def update_payment_method(
    request: UpdatePaymentMethodRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the default payment method for a customer's subscriptions."""
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not getattr(user, 'stripe_customer_id', None):
            raise HTTPException(status_code=404, detail="No Stripe customer found")
        
        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=user.stripe_customer_id,
            set_as_default=True
        )
        return {"status": "success", "message": "Payment method updated successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/payment-methods")
async def get_payment_methods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all saved payment methods for the current user."""
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not getattr(user, 'stripe_customer_id', None):
            return {"payment_methods": []}
        payment_methods = StripeService.get_customer_payment_methods(user.stripe_customer_id)
        return {"payment_methods": payment_methods}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/subscription/{user_id}")
async def get_user_subscription(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a user's active subscription record."""
    current_user_id = extract_user_id(current_user)
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    subscription = db.query(Subscriptions).filter(
        Subscriptions.user_id == user_id,
        Subscriptions.status == "completed",
        Subscriptions.end_date > datetime.utcnow()
    ).order_by(Subscriptions.created_at.desc()).first()
    
    if not subscription:
        return {"message": "No active subscription found"}
    return subscription


@router.post("/remove-card")
async def remove_card(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove the user's saved card from Stripe and clear local record."""
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not getattr(user, 'stripe_payment_method_id', None):
            raise HTTPException(status_code=400, detail="No saved card found")
        
        try:
            StripeService.detach_payment_method(user.stripe_payment_method_id)
        except Exception as e:
            logger.warning(f"⚠️ Could not detach from Stripe (may already be detached): {str(e)}")
        
        user.stripe_payment_method_id = None
        user.card_last4 = None
        user.card_brand = None
        user.card_exp_month = None
        user.card_exp_year = None
        user.card_saved_at = None
        
        db.commit()
        return {"status": "success", "message": "Card removed successfully"}
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))