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


# =============================================================================
# BILLING FLOW
# =============================================================================
#
# APP_MODE = "beta"
#   save-card-beta → save card only, NO charge, mark is_beta_user=True.
#
# APP_MODE = "launch"
#   save-card-beta → resolve the user's Stripe state into one of three cases:
#
#   CASE A — sub active in Stripe + valid DB record
#     → already subscribed; card update only.
#
#   CASE B — sub active in Stripe, NO valid DB record
#     → previous session created the Stripe sub but our DB write never finished
#       (crash, abandoned 3DS, etc). MUST NOT call Subscription.create() —
#       Stripe rejects it with "cannot combine currencies". Instead: adopt the
#       existing sub by writing the missing DB record.
#
#   CASE C — no active Stripe sub
#     → cancel any stale incomplete sub, then create a fresh subscription.
#     → incomplete (3DS) → return requires_action, no DB record yet
#     → active → write DB record, mark user active
#
# =============================================================================


def get_stripe_price_id(plan_type: str) -> str:
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
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_str = secrets.token_hex(4).upper()
    return f"{prefix}-{timestamp}-{random_str}"


def extract_user_id(current_user) -> int:
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


def resolve_stripe_subscription_state(user: User, db: Session) -> dict:
    """
    Determines the user's true subscription state by cross-referencing
    Stripe and our local DB. Returns a dict:

      {
        "case": "fully_subscribed" | "stripe_active_no_db" | "needs_new_sub",
        "stripe_sub": <Stripe sub dict or None>,
        "stripe_sub_id": <str or None>,
      }

    CASE MEANINGS
    ─────────────
    "fully_subscribed"
        Stripe active/trialing AND valid non-expired DB record → card update only.

    "stripe_active_no_db"
        Stripe active/trialing BUT no matching DB record.
        A previous session created the Stripe sub but our DB write never
        completed. DO NOT call Subscription.create() — Stripe will reject
        with a currency conflict. Adopt the existing sub instead.

    "needs_new_sub"
        No live Stripe subscription → safe to call Subscription.create().
        Caller must cancel any stale incomplete sub first.
    """
    sub_id = str(getattr(user, 'stripe_subscription_id', '') or '').strip()

    if not sub_id:
        return {"case": "needs_new_sub", "stripe_sub": None, "stripe_sub_id": None}

    try:
        stripe_sub = StripeService.retrieve_subscription(sub_id)
        stripe_status = stripe_sub.get("status", "")
    except Exception as e:
        logger.warning(f"⚠️ Could not retrieve sub {sub_id} from Stripe: {e} — needs_new_sub")
        return {"case": "needs_new_sub", "stripe_sub": None, "stripe_sub_id": sub_id}

    if stripe_status not in ("active", "trialing"):
        logger.info(f"ℹ️ Sub {sub_id} status='{stripe_status}' in Stripe — needs_new_sub")
        return {"case": "needs_new_sub", "stripe_sub": stripe_sub, "stripe_sub_id": sub_id}

    # Stripe says active — check our DB.
    # A "fully_subscribed" result means we have a valid, non-expired active row
    # for this exact sub_id. Any other situation (expired row, wrong status, or
    # no row at all) routes to stripe_active_no_db, where _create_active_subscription_record
    # will safely UPSERT (UPDATE existing row or INSERT new row) without hitting
    # the unique constraint on transaction_id.
    valid_record = db.query(Subscriptions).filter(
        Subscriptions.user_id == user.id,
        Subscriptions.transaction_id == sub_id,
        Subscriptions.subscription_status == "active",
        Subscriptions.end_date > datetime.utcnow()
    ).first()

    if valid_record:
        logger.info(f"✅ Sub {sub_id} active in Stripe + valid DB record — fully_subscribed")
        return {"case": "fully_subscribed", "stripe_sub": stripe_sub, "stripe_sub_id": sub_id}

    # Row exists but expired/wrong-status, OR no row — route to adopt.
    # _create_active_subscription_record will UPDATE or INSERT safely.
    logger.info(
        f"⚠️ Sub {sub_id} active in Stripe, DB record missing or stale — "
        f"stripe_active_no_db (will upsert)"
    )
    return {"case": "stripe_active_no_db", "stripe_sub": stripe_sub, "stripe_sub_id": sub_id}


def get_subscription_dates_from_stripe(subscription_result: dict, plan_type: str):
    """Always prefer Stripe's authoritative period timestamps."""
    period_start = subscription_result.get("current_period_start")
    period_end = subscription_result.get("current_period_end")

    if not (period_start and period_end):
        latest_invoice = subscription_result.get("latest_invoice")
        if isinstance(latest_invoice, dict):
            for line in latest_invoice.get("lines", {}).get("data", []):
                if line.get("period"):
                    period_start = period_start or line["period"].get("start")
                    period_end = period_end or line["period"].get("end")
                    if period_start and period_end:
                        break

    if period_start and period_end:
        try:
            start_date = datetime.fromtimestamp(int(period_start))
            end_date = datetime.fromtimestamp(int(period_end))
            logger.info(f"📅 Stripe period: {start_date} → {end_date}")
            return start_date, end_date
        except (ValueError, TypeError, OverflowError) as e:
            logger.warning(f"⚠️ Could not parse Stripe timestamps: {e}")

    start = datetime.utcnow()
    delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
    return start, start + timedelta(days=delta_map.get(plan_type, 30))


def _create_active_subscription_record(db, user, sub_result, plan_type, amount, tx_ref_prefix="SUB"):
    """
    Upsert a Subscriptions DB record and update user fields for an active sub.
    Only call when status is 'active' or 'trialing'. Never for 'incomplete'.

    sub_result may be either:
      - Our StripeService dict  → has key "subscription_id"
      - A raw Stripe sub dict   → has key "id"

    UPSERT logic: if a row already exists for this transaction_id (e.g. a
    previously expired or incomplete record written by a webhook or prior
    session), UPDATE it in-place rather than INSERT. This prevents the
    unique constraint violation on ix_subscriptions_transaction_id.
    """
    sub_id = sub_result.get("subscription_id") or sub_result.get("id")
    start_date, end_date = get_subscription_dates_from_stripe(sub_result, plan_type)

    # Check for any existing row with this transaction_id (any status)
    existing = db.query(Subscriptions).filter(
        Subscriptions.transaction_id == sub_id
    ).first()

    if existing:
        # Update the existing row to active/completed with fresh dates
        existing.subscription_plan = plan_type
        existing.status = "completed"
        existing.subscription_status = "active"
        existing.amount = Decimal(str(amount))
        existing.start_date = start_date
        existing.end_date = end_date
        subscription = existing
        logger.info(f"♻️ Updated existing subscription record id={existing.id} for sub {sub_id}")
    else:
        subscription = Subscriptions(
            user_id=user.id,
            subscription_plan=plan_type,
            transaction_id=sub_id,
            tx_ref=generate_tx_ref(tx_ref_prefix),
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

    if hasattr(user, 'stripe_subscription_id'):
        user.stripe_subscription_id = sub_id
    if hasattr(user, 'subscription_status'):
        user.subscription_status = "active"
    if hasattr(user, 'subscription_plan'):
        user.subscription_plan = plan_type
    if hasattr(user, 'subscription_expires_at'):
        user.subscription_expires_at = end_date

    from subscriptions.commission_service import CommissionService
    CommissionService.calculate_commission(subscription=subscription, db=db)

    return subscription, end_date


# =============================================================================
# STRIPE CONFIG
# =============================================================================

@router.get("/config")
async def get_stripe_config():
    publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY")
    if not publishable_key:
        raise HTTPException(status_code=500, detail="Stripe configuration not found")
    return {"publishableKey": publishable_key}


# =============================================================================
# SUBSCRIPTION HISTORY
# =============================================================================

@router.get("/history", response_model=list[SubscriptionResponse])
async def get_subscription_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
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


# =============================================================================
# LEGACY PAYMENT INTENT (one-time charge — NOT subscriptions)
# =============================================================================

@router.post("/create-payment-intent", response_model=PaymentIntentResponse)
async def create_payment_intent(
    payment_data: PaymentIntentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """LEGACY: one-time charge only. Do NOT use for subscriptions."""
    try:
        user_id = extract_user_id(current_user)
        if int(payment_data.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        tx_ref = generate_tx_ref("STRIPE")
        intent = StripeService.create_payment_intent(
            amount=payment_data.amount, currency="usd",
            customer_email=payment_data.email,
            metadata={
                "user_id": str(payment_data.user_id),
                "plan_type": payment_data.plan_type,
                "customer_name": payment_data.name,
                "tx_ref": tx_ref,
                "legacy_payment_intent": "true"
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
    """LEGACY: verify a one-time PaymentIntent."""
    try:
        user_id = extract_user_id(current_user)
        if int(payment_verify.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        verification = StripeService.verify_payment(payment_verify.payment_intent_id)
        if verification["status"] != "succeeded":
            raise HTTPException(status_code=400, detail=f"Payment not successful: {verification['status']}")
        existing_sub = db.query(Subscriptions).filter(
            Subscriptions.transaction_id == payment_verify.payment_intent_id
        ).first()
        if existing_sub:
            return existing_sub
        metadata = verification.get("metadata", {})
        plan_type = metadata.get("plan_type", "monthly")
        tx_ref = metadata.get("tx_ref", generate_tx_ref("STRIPE"))
        start_date = datetime.utcnow()
        delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
        end_date = start_date + timedelta(days=delta_map.get(plan_type, 30))
        subscription = Subscriptions(
            user_id=payment_verify.user_id, subscription_plan=plan_type,
            transaction_id=payment_verify.payment_intent_id, tx_ref=tx_ref,
            amount=Decimal(str(verification.get("amount", 0))),
            currency=verification.get("currency", "USD").upper(),
            status="completed", subscription_status="active",
            payment_provider="stripe", start_date=start_date, end_date=end_date
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
                user.email, user.name, float(verification.get("amount", 0)),
                plan_type, end_date.strftime("%B %d, %Y")
            )
        return subscription
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# SAVE CARD / CHECKOUT
# =============================================================================

@router.post("/save-card-beta")
async def save_card_for_beta(
    request: SaveCardRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    BETA MODE  → save card only, no charge, mark user as beta user.
    LAUNCH MODE → resolve Stripe state (3 cases), bill appropriately.

    See resolve_stripe_subscription_state() for the three-case logic.
    The critical rule: NEVER call Subscription.create() when the customer
    already has an active sub in Stripe — even if our DB record is missing.
    """
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        app_mode = BetaService.get_app_mode()

        logger.info(
            f"💳 save-card-beta: user={user.email} (id={user_id}), "
            f"app_mode='{app_mode}', "
            f"is_beta_user={getattr(user, 'is_beta_user', False)}, "
            f"stripe_sub_id='{str(getattr(user, 'stripe_subscription_id', '') or '').strip() or 'none'}'"
        )

        # ── Attach card ───────────────────────────────────────────────────────
        customer_id = StripeService.get_or_create_customer(
            user_id=user_id, email=user.email, name=user.name,
            stripe_customer_id=getattr(user, 'stripe_customer_id', None)
        )
        if not getattr(user, 'stripe_customer_id', None):
            user.stripe_customer_id = customer_id

        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=customer_id,
            set_as_default=True
        )

        # ── Save card metadata ────────────────────────────────────────────────
        payment_method = stripe.PaymentMethod.retrieve(request.payment_method_id)
        user.stripe_payment_method_id = request.payment_method_id
        user.card_last4 = payment_method.card.last4
        user.card_brand = payment_method.card.brand
        user.card_exp_month = payment_method.card.exp_month
        user.card_exp_year = payment_method.card.exp_year
        user.card_saved_at = datetime.utcnow()

        requested_plan = (
            getattr(request, 'plan_type', None)
            or getattr(user, 'subscription_plan', None)
            or "monthly"
        )
        if hasattr(user, 'subscription_plan'):
            user.subscription_plan = requested_plan

        # =====================================================================
        # BETA MODE — save card, no charge
        # =====================================================================
        if app_mode == "beta":
            if hasattr(user, 'is_beta_user'):
                user.is_beta_user = True
            BetaService.mark_as_beta_user(user, db)
            db.commit()
            logger.info(f"✅ Beta card saved for user {user.id} — no charge")
            background_tasks.add_task(
                email_service.send_beta_card_saved_email,
                user.email, user.name, user.card_last4, user.card_brand,
                BetaService.get_grace_period_days()
            )
            NotificationService.create_notification(
                db=db, user_id=user.id, type="card_saved",
                title="✅ Card Saved Successfully",
                message="Your card is securely saved. You'll be billed when we launch — no charge today!",
                link="/dashboard"
            )
            return {
                "status": "success",
                "message": "Card saved. You will be charged at launch.",
                "card_info": {
                    "last4": user.card_last4, "brand": user.card_brand,
                    "exp_month": user.card_exp_month, "exp_year": user.card_exp_year
                },
                "grace_period_days": BetaService.get_grace_period_days(),
                "grace_period_ends": user.grace_period_ends_at.isoformat() if user.grace_period_ends_at else None
            }

        # =====================================================================
        # LAUNCH MODE — three-case resolution
        # =====================================================================
        plan_type = requested_plan
        price_id = get_stripe_price_id(plan_type)
        if not price_id:
            raise HTTPException(
                status_code=400,
                detail=f"No Stripe price configured for plan '{plan_type}'. "
                       f"Set STRIPE_{plan_type.upper()}_PRICE_ID in environment."
            )

        from api.routes.control.settings import get_settings
        settings = get_settings(db=db, current_user=user)
        price_map = {
            "monthly": settings.monthly_price,
            "quarterly": settings.quarterly_price,
            "yearly": settings.yearly_price
        }
        amount = price_map.get(plan_type, 29.95)

        state = resolve_stripe_subscription_state(user, db)
        logger.info(f"🔍 Stripe state for user {user.id}: case='{state['case']}'")

        # ── CASE A: Genuinely subscribed — card update only ───────────────────
        if state["case"] == "fully_subscribed":
            db.commit()
            logger.info(f"ℹ️ User {user.id} fully subscribed — card updated only")
            NotificationService.create_notification(
                db=db, user_id=user.id, type="card_updated",
                title="✅ Card Updated",
                message="Your payment card has been updated.",
                link="/dashboard"
            )
            return {
                "status": "success",
                "message": "Card updated. Your subscription remains active.",
                "card_info": {
                    "last4": user.card_last4, "brand": user.card_brand,
                    "exp_month": user.card_exp_month, "exp_year": user.card_exp_year
                }
            }

        # ── CASE B: Active in Stripe, missing DB record — adopt it ───────────
        # The Stripe subscription already exists and is paid. Our DB write never
        # completed in a previous session. Write the missing record now.
        # NEVER call Subscription.create() here — Stripe rejects with currency conflict.
        if state["case"] == "stripe_active_no_db":
            stripe_sub = state["stripe_sub"]
            subscription, end_date = _create_active_subscription_record(
                db=db, user=user, sub_result=stripe_sub,
                plan_type=plan_type, amount=amount, tx_ref_prefix="ADOPT"
            )
            db.commit()
            db.refresh(subscription)
            logger.info(
                f"✅ Adopted existing Stripe sub {state['stripe_sub_id']} "
                f"for user {user.id}, expires {end_date}"
            )
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                plan_type, end_date.strftime("%B %d, %Y")
            )
            NotificationService.create_notification(
                db=db, user_id=user.id, type="subscription_active",
                title="🎉 Subscription Activated!",
                message=f"Your {plan_type} subscription is active until {end_date.strftime('%B %d, %Y')}.",
                link="/dashboard"
            )
            return {
                "status": "success",
                "message": "Subscription activated successfully.",
                "card_info": {
                    "last4": user.card_last4, "brand": user.card_brand,
                    "exp_month": user.card_exp_month, "exp_year": user.card_exp_year
                }
            }

        # ── CASE C: No active Stripe sub — create a fresh one ────────────────
        # Cancel any stale incomplete sub first so Stripe doesn't complain.
        stale_sub_id = state["stripe_sub_id"]
        if stale_sub_id:
            try:
                stale_status = (state["stripe_sub"] or {}).get("status", "")
                if stale_status == "incomplete":
                    stripe.Subscription.delete(stale_sub_id)
                    logger.info(f"🗑️ Cancelled stale incomplete sub {stale_sub_id}")
            except Exception as cancel_err:
                logger.warning(f"⚠️ Could not cancel stale sub {stale_sub_id}: {cancel_err}")
            if hasattr(user, 'stripe_subscription_id'):
                user.stripe_subscription_id = None

        logger.info(
            f"🚀 [LAUNCH] Creating new subscription for user {user.id} ({user.email}), "
            f"plan='{plan_type}', price='{price_id}'"
        )

        sub_result = StripeService.create_subscription_with_saved_card(
            customer_id=customer_id,
            price_id=price_id,
            payment_method_id=request.payment_method_id,
            metadata={
                "user_id": str(user.id),
                "plan_type": plan_type,
                "source": "save_card_launch",
                "is_beta_user": str(getattr(user, 'is_beta_user', False))
            },
            off_session=False  # User is present — allows 3DS modal
        )

        sub_status = sub_result.get("status")
        logger.info(
            f"   Stripe result: status='{sub_status}', "
            f"sub_id='{sub_result.get('subscription_id')}', "
            f"has_client_secret={bool(sub_result.get('client_secret'))}"
        )

        # 3DS required — commit card save, return requires_action.
        # Do NOT create a DB record — the sub has no valid billing dates yet.
        # Frontend: stripe.confirmCardPayment(client_secret) → POST /confirm-subscription
        if sub_status == "incomplete":
            if not sub_result.get("client_secret"):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "3DS required but Stripe did not return a client_secret. "
                        f"Sub ID: {sub_result.get('subscription_id', '?')}"
                    )
                )
            if hasattr(user, 'stripe_subscription_id'):
                user.stripe_subscription_id = sub_result["subscription_id"]
            db.commit()
            logger.info(f"🔐 3DS required for user {user.id}, sub={sub_result.get('subscription_id')}")
            return {
                "status": "requires_action",
                "subscription_id": sub_result["subscription_id"],
                "payment_intent_id": sub_result.get("payment_intent_id"),
                "client_secret": sub_result.get("client_secret"),
                "message": "Additional authentication required to complete payment."
            }

        # Subscription active immediately — write DB record
        if sub_status in ("active", "trialing"):
            subscription, end_date = _create_active_subscription_record(
                db=db, user=user, sub_result=sub_result,
                plan_type=plan_type, amount=amount, tx_ref_prefix="LAUNCH"
            )
            db.commit()
            db.refresh(subscription)
            logger.info(f"✅ New subscription active for user {user.id}, expires {end_date}")
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                plan_type, end_date.strftime("%B %d, %Y")
            )
            NotificationService.create_notification(
                db=db, user_id=user.id, type="subscription_active",
                title="🎉 Subscription Activated!",
                message=f"Your {plan_type} subscription is active until {end_date.strftime('%B %d, %Y')}.",
                link="/dashboard"
            )
            return {
                "status": "success",
                "message": "Subscription activated successfully.",
                "card_info": {
                    "last4": user.card_last4, "brand": user.card_brand,
                    "exp_month": user.card_exp_month, "exp_year": user.card_exp_year
                }
            }

        raise HTTPException(
            status_code=400,
            detail=f"Unexpected Stripe subscription status: '{sub_status}'"
        )

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


# =============================================================================
# BETA STATUS
# =============================================================================

@router.get("/beta/status")
async def get_beta_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        status = BetaService.get_user_status(user)

        if status.get("show_card_info") and user.card_last4:
            status["card_info"] = {
                "last4": user.card_last4, "brand": user.card_brand,
                "exp_month": user.card_exp_month, "exp_year": user.card_exp_year
            }

        status["is_beta_mode"] = BetaService.is_beta_mode()
        status["is_in_grace_period"] = BetaService.is_in_grace_period(user)
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# WEBHOOK
# =============================================================================

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db)
):
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

        logger.info(f"📨 Webhook: {event.type}")

        if event.type == "invoice.payment_succeeded":
            invoice = event.data.object

            # ----------------------------------------------------------------
            # Stripe API 2025-03-31 (basil) moved the subscription reference
            # out of invoice.subscription into invoice.parent.subscription_details.subscription
            # We try all known locations in order.
            # ----------------------------------------------------------------

            subscription_id = None
            payment_intent_id = getattr(invoice, 'payment_intent', None)

            # Location 1 (old API / direct field — may still be present)
            subscription_id = getattr(invoice, 'subscription', None) or None

            # Location 2 (basil API): invoice.parent.subscription_details.subscription
            if not subscription_id:
                parent = getattr(invoice, 'parent', None)
                if parent:
                    sub_details = getattr(parent, 'subscription_details', None)
                    if sub_details:
                        subscription_id = getattr(sub_details, 'subscription', None)
                        if subscription_id:
                            logger.info(f"ℹ️ subscription_id from invoice.parent.subscription_details: {subscription_id}")

            # Location 3: invoice.lines.data[].parent.subscription_item_details.subscription
            if not subscription_id:
                lines = getattr(getattr(invoice, 'lines', None), 'data', []) or []
                for line in lines:
                    line_parent = getattr(line, 'parent', None)
                    if line_parent:
                        sid_details = getattr(line_parent, 'subscription_item_details', None)
                        if sid_details:
                            subscription_id = getattr(sid_details, 'subscription', None)
                            if subscription_id:
                                logger.info(f"ℹ️ subscription_id from line item parent: {subscription_id}")
                                break

            # Location 4: metadata on line items (last resort)
            if not subscription_id:
                lines = getattr(getattr(invoice, 'lines', None), 'data', []) or []
                for line in lines:
                    meta = getattr(line, 'metadata', None) or {}
                    if hasattr(meta, 'get'):
                        sid = meta.get('subscription') or meta.get('subscription_id')
                        if sid:
                            subscription_id = sid
                            logger.info(f"ℹ️ subscription_id from line metadata: {subscription_id}")
                            break

            # Location 5: customer lookup fallback
            if not subscription_id:
                cid = getattr(invoice, 'customer', None)
                if cid:
                    try:
                        subs = stripe.Subscription.list(customer=cid, status="active", limit=1)
                        if subs and subs.data:
                            subscription_id = subs.data[0].id
                            logger.info(f"ℹ️ subscription_id resolved via customer {cid}: {subscription_id}")
                    except Exception as lookup_err:
                        logger.warning(f"⚠️ Could not resolve subscription via customer: {lookup_err}")

            if not subscription_id:
                logger.warning(
                    f"⚠️ invoice.payment_succeeded: no subscription_id found anywhere "                    f"(customer={getattr(invoice, 'customer', 'unknown')}, "                    f"payment_intent={payment_intent_id or 'none'}) — skipping"
                )
                return {"status": "success"}

            # basil API: payment_intent moved to invoice.payment_intent still exists
            # but may be null on test clocks. Fall back to the charge ID or invoice ID
            # for idempotency purposes so we don't skip real renewals.
            if not payment_intent_id:
                # Try getting it from the charges on the invoice
                charge_id = getattr(invoice, 'charge', None)
                if charge_id:
                    payment_intent_id = charge_id
                    logger.info(f"ℹ️ Using charge_id as transaction_id: {payment_intent_id}")

            if not payment_intent_id:
                # Use invoice ID itself — guaranteed unique, safe for idempotency
                invoice_id = getattr(invoice, 'id', None)
                if invoice_id:
                    payment_intent_id = invoice_id
                    logger.info(f"ℹ️ Using invoice_id as transaction_id: {payment_intent_id}")

            if not payment_intent_id:
                logger.warning(f"⚠️ No transaction identifier found for sub {subscription_id} — skipping")
                return {"status": "success"}

            # Retrieve subscription for period dates and metadata
            stripe_sub = stripe.Subscription.retrieve(subscription_id)

            # ----------------------------------------------------------------
            # Period dates — try 3 sources in order of reliability:
            # 1. invoice.lines.data[0].period  (most reliable in basil API)
            # 2. stripe_sub.current_period_start/end
            # 3. Calculated fallback from plan_type
            # The event data shows dates live in lines[0].period for basil API.
            # ----------------------------------------------------------------
            period_start = None
            period_end   = None

            # Source 1: line item period (basil API puts dates here)
            lines = getattr(getattr(invoice, 'lines', None), 'data', []) or []
            for line in lines:
                lp = getattr(line, 'period', None)
                if lp:
                    period_start = getattr(lp, 'start', None)
                    period_end   = getattr(lp, 'end',   None)
                if period_start and period_end:
                    logger.info(f"📅 Period from line item: {period_start} → {period_end}")
                    break

            # Source 2: subscription object
            if not (period_start and period_end):
                period_start = getattr(stripe_sub, 'current_period_start', None)
                period_end   = getattr(stripe_sub, 'current_period_end',   None)
                if period_start and period_end:
                    logger.info(f"📅 Period from subscription object: {period_start} → {period_end}")

            if period_start and period_end:
                start_date = datetime.fromtimestamp(int(period_start))
                end_date   = datetime.fromtimestamp(int(period_end))
            else:
                logger.warning(f"⚠️ Could not determine period for sub {subscription_id} — using fallback dates")
                start_date = datetime.utcnow()
                sub_meta_check = getattr(stripe_sub, 'metadata', None) or {}
                plan_fallback = sub_meta_check.get("plan_type", "monthly")
                delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
                end_date = start_date + timedelta(days=delta_map.get(plan_fallback, 30))

            logger.info(f"📅 Renewal period: {start_date.date()} → {end_date.date()}")

            # ----------------------------------------------------------------
            # Find user — 5 strategies, log which one succeeds.
            # The basil API puts user_id in line item metadata, so check there too.
            # ----------------------------------------------------------------
            user = db.query(User).filter(User.stripe_subscription_id == subscription_id).first()
            if user:
                logger.info(f"👤 User found via stripe_subscription_id: {user.email}")

            if not user:
                uid = (getattr(invoice, 'metadata', None) or {}).get("user_id")
                if uid:
                    user = db.query(User).filter(User.id == int(uid)).first()
                    if user:
                        logger.info(f"👤 User found via invoice metadata user_id={uid}: {user.email}")

            # basil API: user_id is in invoice.parent.subscription_details.metadata
            if not user:
                parent = getattr(invoice, 'parent', None)
                sub_details = getattr(parent, 'subscription_details', None) if parent else None
                parent_meta = getattr(sub_details, 'metadata', None) if sub_details else None
                uid = (parent_meta or {}).get("user_id") if parent_meta else None
                if uid:
                    user = db.query(User).filter(User.id == int(uid)).first()
                    if user:
                        logger.info(f"👤 User found via parent.subscription_details metadata user_id={uid}: {user.email}")

            # basil API: user_id is also in line item metadata
            if not user:
                for line in lines:
                    line_meta = getattr(line, 'metadata', None) or {}
                    uid = line_meta.get("user_id") if hasattr(line_meta, 'get') else None
                    if uid:
                        user = db.query(User).filter(User.id == int(uid)).first()
                        if user:
                            logger.info(f"👤 User found via line item metadata user_id={uid}: {user.email}")
                            break

            if not user:
                sub_meta = getattr(stripe_sub, 'metadata', None) or {}
                uid = sub_meta.get("user_id")
                if uid:
                    user = db.query(User).filter(User.id == int(uid)).first()
                    if user:
                        logger.info(f"👤 User found via sub metadata user_id={uid}: {user.email}")
                        if hasattr(user, 'stripe_subscription_id'):
                            user.stripe_subscription_id = subscription_id

            if not user:
                cid = getattr(invoice, 'customer', None)
                if cid:
                    user = db.query(User).filter(User.stripe_customer_id == cid).first()
                    if user:
                        logger.info(f"👤 User found via customer_id {cid}: {user.email}")

            if not user:
                logger.warning(
                    f"⚠️ No user found for subscription {subscription_id} "                    f"(customer={getattr(invoice, 'customer', 'unknown')}) — skipping"
                )
                return {"status": "success"}

            if hasattr(user, 'stripe_subscription_id') and user.stripe_subscription_id != subscription_id:
                user.stripe_subscription_id = subscription_id

            # Idempotency — skip if already recorded
            existing = db.query(Subscriptions).filter(
                Subscriptions.transaction_id == payment_intent_id
            ).first()
            if existing:
                logger.info(f"ℹ️ Invoice {payment_intent_id} already recorded — skipping")
                return {"status": "success"}

            sub_meta = getattr(stripe_sub, 'metadata', None) or {}
            plan_type = sub_meta.get("plan_type") or getattr(user, 'subscription_plan', None) or "monthly"

            user.subscription_status = "active"
            user.subscription_expires_at = end_date
            if hasattr(user, 'subscription_plan'):
                user.subscription_plan = plan_type

            amount_paid = getattr(invoice, 'amount_paid', 0) or 0
            currency = getattr(invoice, 'currency', 'usd') or 'usd'

            new_sub = Subscriptions(
                user_id=user.id, subscription_plan=plan_type,
                transaction_id=payment_intent_id,
                tx_ref=f"RENEW-{user.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                amount=Decimal(str(amount_paid / 100)),
                currency=currency.upper(),
                status="completed", subscription_status="active",
                payment_provider="stripe", start_date=start_date, end_date=end_date
            )
            db.add(new_sub)
            db.flush()

            from subscriptions.commission_service import CommissionService
            CommissionService.calculate_commission(subscription=new_sub, db=db)
            db.commit()
            logger.info(f"✅ Renewal recorded: user={user.email} (id={user.id}), plan={plan_type}, {start_date.date()} → {end_date.date()}")

            NotificationService.create_notification(
                db=db, user_id=user.id, type="subscription_renewed",
                title="✅ Subscription Renewed",
                message=f"Your {plan_type} subscription has been renewed until {end_date.strftime('%B %d, %Y')}.",
                link="/dashboard"
            )
            db.commit()

        elif event.type == "invoice.payment_failed":
            invoice = event.data.object
            sub_id = getattr(invoice, 'subscription', None)
            if sub_id:
                user = db.query(User).filter(
                    User.stripe_subscription_id == sub_id
                ).first()
                if not user:
                    # Fallback: find by customer
                    cid = getattr(invoice, 'customer', None)
                    if cid:
                        user = db.query(User).filter(User.stripe_customer_id == cid).first()
                if user:
                    logger.warning(f"⚠️ Payment failed for user {user.id}, sub {sub_id}")
                    NotificationService.create_notification(
                        db=db, user_id=user.id,
                        type="payment_failed",
                        title="⚠️ Payment Failed",
                        message="Your subscription payment failed. Please update your payment method to keep your access.",
                        link="/dashboard/upgrade"
                    )
                    db.commit()

        elif event.type == "customer.subscription.deleted":
            stripe_sub = event.data.object
            user = db.query(User).filter(User.stripe_subscription_id == stripe_sub.id).first()
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
                    db=db, user_id=user.id, type="subscription_cancelled",
                    title="Subscription Cancelled",
                    message="Your subscription has been cancelled.",
                    link="/dashboard/upgrade"
                )
                db.commit()

        elif event.type == "customer.subscription.updated":
            stripe_sub = event.data.object
            user = None
            uid = (getattr(stripe_sub, 'metadata', {}) or {}).get("user_id")
            if uid:
                user = db.query(User).filter(User.id == int(uid)).first()
            if not user and stripe_sub.customer:
                user = db.query(User).filter(User.stripe_customer_id == stripe_sub.customer).first()
            if not user:
                user = db.query(User).filter(User.stripe_subscription_id == stripe_sub.id).first()
            if user:
                status_map = {
                    "active": "active", "past_due": "past_due",
                    "unpaid": "unpaid", "canceled": "cancelled", "trialing": "active"
                }
                mapped = status_map.get(stripe_sub.status)
                if mapped and hasattr(user, 'subscription_status'):
                    user.subscription_status = mapped
                db.commit()

        elif event.type == "payment_intent.succeeded":
            payment_intent = event.data.object
            metadata = payment_intent.metadata or {}
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
                    user_id=user_id, subscription_plan=plan_type,
                    transaction_id=payment_intent.id, tx_ref=tx_ref,
                    amount=Decimal(str(payment_intent.amount / 100)),
                    currency=payment_intent.currency.upper(),
                    status="completed", subscription_status="active",
                    payment_provider="stripe", start_date=start, end_date=end
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

        elif event.type == "payout.paid":
            handle_payout_paid(event, db)
        elif event.type in ("payout.failed", "payout.canceled"):
            handle_payout_failed(event, db)

        return {"status": "success"}

    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    except Exception as e:
        event_type = event.type if 'event' in locals() else 'unknown'
        logger.error(f"❌ Webhook error [{event_type}]: {str(e)}")
        traceback.print_exc()
        # Return 200 so Stripe does not keep retrying unhandled/unknown events.
        # Only signature failures warrant a 400.
        return {"status": "error", "detail": str(e)}


def handle_payout_paid(event: dict, db: Session):
    stripe_payout = event.data.object
    internal_payout_id = (stripe_payout.get("metadata") or {}).get("stripe_connect_payout_id")
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
    internal_payout_id = (stripe_payout.get("metadata") or {}).get("stripe_connect_payout_id")
    if not internal_payout_id:
        return
    from subscriptions.payout_service import PayoutService
    PayoutService.reverse_payout(
        internal_payout_id,
        stripe_payout.get("failure_message") or "Stripe payout failed",
        db
    )


# =============================================================================
# CREATE SUBSCRIPTION WITH SAVED CARD (explicit checkout for returning users)
# =============================================================================

@router.post("/create-subscription-with-saved-card")
async def create_subscription_with_saved_card(
    request: CreateSubscriptionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        price_id = get_stripe_price_id(request.plan_type)
        if not price_id:
            raise HTTPException(status_code=400, detail=f"Price not configured for plan: {request.plan_type}")

        customer_id = StripeService.get_or_create_customer(
            user_id=user_id, email=user.email, name=user.name,
            stripe_customer_id=getattr(user, 'stripe_customer_id', None)
        )
        if not getattr(user, 'stripe_customer_id', None) and hasattr(user, 'stripe_customer_id'):
            user.stripe_customer_id = customer_id
            db.commit()

        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=customer_id, set_as_default=True
        )

        from api.routes.control.settings import get_settings
        settings = get_settings(db=db, current_user=user)
        price_map = {
            "monthly": settings.monthly_price,
            "quarterly": settings.quarterly_price,
            "yearly": settings.yearly_price
        }
        amount = price_map.get(request.plan_type, 29.95)

        state = resolve_stripe_subscription_state(user, db)
        logger.info(f"🔍 create-sub state for user {user.id}: case='{state['case']}'")

        # Fully subscribed — update plan only
        if state["case"] == "fully_subscribed":
            try:
                updated_sub = StripeService.update_subscription_price(
                    subscription_id=state["stripe_sub_id"],
                    new_price_id=price_id, prorate=True
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
                return {"status": "active", "subscription_id": updated_sub["id"], "message": "Subscription updated"}
            except Exception:
                pass

        # Active in Stripe but missing DB — adopt
        if state["case"] == "stripe_active_no_db":
            subscription, end_date = _create_active_subscription_record(
                db=db, user=user, sub_result=state["stripe_sub"],
                plan_type=request.plan_type, amount=amount, tx_ref_prefix="ADOPT"
            )
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
            db.commit()
            db.refresh(subscription)
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                request.plan_type, end_date.strftime("%B %d, %Y")
            )
            return {"status": "active", "subscription_id": state["stripe_sub_id"], "subscription": subscription}

        # Needs new sub — cancel stale incomplete first
        stale_sub_id = state["stripe_sub_id"]
        if stale_sub_id:
            try:
                stale_status = (state["stripe_sub"] or {}).get("status", "")
                if stale_status == "incomplete":
                    stripe.Subscription.delete(stale_sub_id)
                    logger.info(f"🗑️ Cancelled stale incomplete sub {stale_sub_id}")
                    if hasattr(user, 'stripe_subscription_id'):
                        user.stripe_subscription_id = None
            except Exception:
                pass

        tx_ref = generate_tx_ref("STRIPE-SUB")
        subscription_result = StripeService.create_subscription_with_saved_card(
            customer_id=customer_id, price_id=price_id,
            payment_method_id=request.payment_method_id,
            metadata={"user_id": str(user_id), "plan_type": request.plan_type, "tx_ref": tx_ref}
        )

        if subscription_result["status"] == "active":
            if db.query(Subscriptions).filter(
                Subscriptions.transaction_id == subscription_result["subscription_id"]
            ).first():
                return {"status": "active", "subscription_id": subscription_result["subscription_id"]}

            subscription, end_date = _create_active_subscription_record(
                db=db, user=user, sub_result=subscription_result,
                plan_type=request.plan_type, amount=amount, tx_ref_prefix="STRIPE-SUB"
            )
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
            db.commit()
            db.refresh(subscription)
            background_tasks.add_task(
                email_service.send_payment_success_email,
                user.email, user.name, float(amount),
                request.plan_type, end_date.strftime("%B %d, %Y")
            )
            return {"status": "active", "subscription_id": subscription_result["subscription_id"], "subscription": subscription}

        elif subscription_result["status"] == "incomplete":
            if not subscription_result.get("client_secret"):
                raise HTTPException(status_code=500, detail="3DS required but client_secret missing")
            if hasattr(user, 'stripe_subscription_id'):
                user.stripe_subscription_id = subscription_result["subscription_id"]
            db.commit()
            return {
                "status": "requires_action",
                "subscription_id": subscription_result["subscription_id"],
                "payment_intent_id": subscription_result.get("payment_intent_id"),
                "client_secret": subscription_result.get("client_secret"),
                "message": "Additional authentication required"
            }

        raise HTTPException(status_code=400, detail=f"Unexpected status: {subscription_result['status']}")

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


# =============================================================================
# CONFIRM SUBSCRIPTION  (after 3DS authentication)
# =============================================================================

@router.post("/confirm-subscription")
async def confirm_subscription(
    request: ConfirmSubscriptionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Creates the Subscriptions DB record after 3DS succeeds.
    Called by the frontend after stripe.confirmCardPayment() resolves.
    Works for both save-card-beta and create-subscription-with-saved-card flows.
    """
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        verification = StripeService.verify_payment(request.payment_intent_id)
        if verification["status"] != "succeeded":
            raise HTTPException(
                status_code=400,
                detail=f"Payment not confirmed. Status: {verification['status']}"
            )

        subscription_details = StripeService.retrieve_subscription(request.subscription_id)
        if subscription_details["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Subscription not active after 3DS. Status: {subscription_details['status']}"
            )

        sub_meta = subscription_details.get('metadata') or {}
        plan_type = (
            sub_meta.get("plan_type")
            or verification.get("metadata", {}).get("plan_type")
            or getattr(user, 'subscription_plan', None)
            or "monthly"
        )
        tx_ref = verification.get("metadata", {}).get("tx_ref") or generate_tx_ref("STRIPE-CONFIRM")

        logger.info(
            f"✅ confirm-subscription: user={user.email}, "
            f"sub={request.subscription_id}, plan='{plan_type}'"
        )

        from api.routes.control.settings import get_settings
        settings = get_settings(db=db, current_user=user)
        price_map = {
            "monthly": settings.monthly_price,
            "quarterly": settings.quarterly_price,
            "yearly": settings.yearly_price
        }
        amount = price_map.get(plan_type, 29.95)
        start_date, end_date = get_subscription_dates_from_stripe(subscription_details, plan_type)

        existing = db.query(Subscriptions).filter(
            Subscriptions.transaction_id == request.subscription_id
        ).first()

        if existing:
            existing.subscription_status = "active"
            existing.status = "completed"
            existing.start_date = start_date
            existing.end_date = end_date
            subscription = existing
        else:
            subscription = Subscriptions(
                user_id=user_id, subscription_plan=plan_type,
                transaction_id=request.subscription_id, tx_ref=tx_ref,
                amount=Decimal(str(amount)), currency="USD",
                status="completed", subscription_status="active",
                payment_provider="stripe", start_date=start_date, end_date=end_date
            )
            db.add(subscription)
        db.flush()

        if hasattr(user, 'subscription_status'):
            user.subscription_status = "active"
        if hasattr(user, 'subscription_plan'):
            user.subscription_plan = plan_type
        if hasattr(user, 'subscription_expires_at'):
            user.subscription_expires_at = end_date
        if hasattr(user, 'stripe_subscription_id'):
            user.stripe_subscription_id = request.subscription_id

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

        background_tasks.add_task(
            email_service.send_payment_success_email,
            user.email, user.name, float(amount),
            plan_type, end_date.strftime("%B %d, %Y")
        )
        NotificationService.create_notification(
            db=db, user_id=user_id, type="subscription_active",
            title="🎉 Subscription Activated!",
            message=f"Your subscription is now active until {end_date.strftime('%B %d, %Y')}.",
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


# =============================================================================
# CANCEL / UPDATE / MANAGE
# =============================================================================

@router.post("/cancel-subscription")
async def cancel_subscription_endpoint(
    at_period_end: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        sub_id = str(getattr(user, 'stripe_subscription_id', '') or '').strip()
        if not sub_id:
            raise HTTPException(status_code=404, detail="No active subscription found")
        result = StripeService.cancel_subscription(subscription_id=sub_id, at_period_end=at_period_end)
        sub_record = db.query(Subscriptions).filter(
            Subscriptions.user_id == user_id, Subscriptions.subscription_status == "active"
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
    try:
        user_id = extract_user_id(current_user)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not getattr(user, 'stripe_customer_id', None):
            raise HTTPException(status_code=404, detail="No Stripe customer found")
        StripeService.attach_payment_method(
            payment_method_id=request.payment_method_id,
            customer_id=user.stripe_customer_id, set_as_default=True
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
            logger.warning(f"⚠️ Could not detach from Stripe: {str(e)}")
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