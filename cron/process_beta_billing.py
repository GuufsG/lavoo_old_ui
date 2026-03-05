"""
Handle automatic billing of beta users at launch.
Run once at launch (or daily via cron to catch stragglers).

WHY THIS FILE EXISTS:
    When APP_MODE switches from 'beta' to 'launch', beta users who saved their
    cards during the beta period need to be billed. The save-card-beta endpoint
    handles users who save cards AFTER launch, but users who saved cards BEFORE
    launch need this script to create their Stripe Subscriptions.

WHY SOME SUBSCRIPTIONS WERE CANCELLED (old bug):
    The old script used payment_behavior="default_incomplete" (the default for
    interactive checkout). This creates the subscription in 'incomplete' status
    and waits for a frontend to confirm the PaymentIntent. When no user confirms
    within 23 hours, Stripe automatically cancels the subscription. Since this
    script runs server-side with no user present, all those subscriptions expired.

THE FIX:
    Use payment_behavior="error_if_incomplete" for server-side / off-session billing.
    This tells Stripe to attempt the charge immediately:
    - If card succeeds         → subscription becomes 'active' immediately
    - If card requires 3DS     → CardError raised immediately (no dangling subscription)
    - If card is declined      → CardError raised immediately
    No incomplete subscriptions are ever created, so nothing can expire and cancel.
"""

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
import stripe

sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from db.pg_connections import get_db
from db.pg_models import User, Subscriptions
from subscriptions.beta_service import BetaService
from config.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def get_subscription_dates_from_stripe(subscription_result: dict, plan_type: str):
    """
    Use Stripe's current_period_start/end as authoritative billing dates.
    Falls back to timedelta only if Stripe didn't return period data.
    """
    from datetime import timedelta
    period_start = subscription_result.get("current_period_start")
    period_end = subscription_result.get("current_period_end")
    if period_start and period_end:
        try:
            return (
                datetime.fromtimestamp(int(period_start)),
                datetime.fromtimestamp(int(period_end))
            )
        except (ValueError, TypeError):
            pass
    logger.warning(f"Stripe period data missing for {plan_type}, falling back to timedelta")
    start = datetime.utcnow()
    delta_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
    return start, start + timedelta(days=delta_map.get(plan_type, 30))


def create_subscription_off_session(
    customer_id: str,
    price_id: str,
    payment_method_id: str,
    metadata: dict
) -> dict:
    """
    Create a Stripe Subscription for server-side / off-session billing.

    KEY DIFFERENCE from the interactive flow:
        payment_behavior="error_if_incomplete" instead of "default_incomplete"

    default_incomplete (interactive flow):
        - Creates subscription in 'incomplete' status
        - Waits for frontend to confirm the PaymentIntent
        - If not confirmed within 23 hours → Stripe CANCELS the subscription
        - Correct for: user is present on the checkout page

    error_if_incomplete (server-side / cron flow):
        - Attempts to charge the card immediately
        - If charge succeeds → subscription is 'active' right away
        - If card requires 3DS or is declined → raises CardError immediately
        - No incomplete subscription is created, so nothing can expire and cancel
        - Correct for: no user present, automated billing
    """
    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        default_payment_method=payment_method_id,
        payment_behavior="error_if_incomplete",  # ← Critical for server-side billing
        payment_settings={
            "save_default_payment_method": "on_subscription",
            "payment_method_types": ["card"]
        },
        off_session=True,  # Signals to Stripe this charge has no user present
        metadata=metadata,
        expand=["latest_invoice.payment_intent"]
    )

    current_period_end = getattr(subscription, 'current_period_end', None)
    current_period_start = getattr(subscription, 'current_period_start', None)

    return {
        "subscription_id": subscription.id,
        "status": subscription.status,
        "current_period_start": current_period_start,
        "current_period_end": current_period_end,
    }


def activate_user(user: User, plan_type: str, result: dict, db: Session):
    """
    Create local DB records and update user status after a successful Stripe billing.
    Shared between the normal success path and the 3DS-not-required path.
    """
    start_date, end_date = get_subscription_dates_from_stripe(result, plan_type)

    from api.routes.control.settings import get_settings
    settings = get_settings(db=db, current_user=user)
    price_map = {
        "monthly": settings.monthly_price,
        "quarterly": settings.quarterly_price,
        "yearly": settings.yearly_price
    }
    amount = price_map.get(plan_type, 29.95)

    # Update user record
    if hasattr(user, 'stripe_subscription_id'):
        user.stripe_subscription_id = result["subscription_id"]
    user.subscription_status = "active"
    user.subscription_plan = plan_type
    user.subscription_expires_at = end_date

    # Create billing record
    subscription = Subscriptions(
        user_id=user.id,
        subscription_plan=plan_type,
        transaction_id=result["subscription_id"],
        tx_ref=f"BETA-LAUNCH-{user.id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
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

    # Calculate commission if applicable
    from subscriptions.commission_service import CommissionService
    CommissionService.calculate_commission(subscription=subscription, db=db)

    # Send success email
    from emailing.email_service import email_service
    email_service.send_payment_success_email(
        user.email, user.name, float(amount),
        plan_type, end_date.strftime("%B %d, %Y")
    )

    # In-app notification
    from api.services.notification_service import NotificationService
    NotificationService.create_notification(
        db=db,
        user_id=user.id,
        type="subscription_active",
        title="Subscription Activated",
        message="Your subscription is now active. Welcome to Lavoo!",
        link="/dashboard/upgrade"
    )

    db.commit()
    logger.info(
        f"✅ [CRON] Activated user {user.id} ({user.email}). "
        f"Plan: {plan_type}, Sub: {result['subscription_id']}, Expires: {end_date}"
    )


def notify_3ds_required(user: User, db: Session):
    """
    Notify a user that their card requires interactive 3DS and cannot be
    charged server-side. They need to visit the billing page to complete payment.
    """
    from api.services.notification_service import NotificationService
    NotificationService.create_notification(
        db=db,
        user_id=user.id,
        type="payment_action_required",
        title="Action Required — Complete Your Payment",
        message=(
            "Your bank requires additional verification to process your subscription. "
            "Please visit the billing page to complete your payment."
        ),
        link="/dashboard/upgrade"
    )
    try:
        from emailing.email_service import email_service
        email_service.send_payment_action_required_email(user.email, user.name)
    except Exception:
        pass  # Email failure shouldn't block the notification
    db.commit()


def process_beta_billing():
    """
    Find all beta users with saved cards who haven't been billed yet,
    and create their Stripe Subscriptions using off-session billing.

    Run this script:
    - Once when switching APP_MODE from 'beta' to 'launch'
    - Daily via cron to catch any users missed in the initial run
    - After fixing failed billing attempts
    """
    db = next(get_db())
    success_count = 0
    failed_count = 0
    skipped_3ds_count = 0

    try:
        # Find unbilled beta users with saved cards
        users_to_charge = db.query(User).filter(
            User.is_beta_user == True,
            User.subscription_status != "active",
            User.stripe_payment_method_id.isnot(None),
            User.stripe_customer_id.isnot(None),  # Must have Stripe customer
        ).all()

        app_mode = BetaService.get_app_mode()
        logger.info(
            f"[{app_mode.upper()}] Beta billing run started. "
            f"Found {len(users_to_charge)} users to process."
        )

        for user in users_to_charge:
            try:
                plan_type = getattr(user, 'subscription_plan', None) or "monthly"
                price_id = os.getenv(f"STRIPE_{plan_type.upper()}_PRICE_ID", "").strip()

                if not price_id:
                    logger.error(f"❌ No price ID for plan '{plan_type}' (user {user.id}). Set STRIPE_{plan_type.upper()}_PRICE_ID.")
                    failed_count += 1
                    continue

                if not user.stripe_customer_id:
                    logger.warning(f"⚠️ User {user.id} has no stripe_customer_id. Skipping.")
                    failed_count += 1
                    continue

                logger.info(f"Billing user {user.id} ({user.email}), plan: {plan_type}")

                # off_session=True → payment_behavior="error_if_incomplete"
                # No user present — charge immediately or fail immediately.
                # Never creates an incomplete subscription that Stripe will cancel after 23h.
                from subscriptions.stripe_service import StripeService
                result = StripeService.create_subscription_with_saved_card(
                    customer_id=user.stripe_customer_id,
                    price_id=price_id,
                    payment_method_id=user.stripe_payment_method_id,
                    metadata={
                        "user_id": str(user.id),
                        "plan_type": plan_type,
                        "source": "beta_launch_cron"
                    },
                    off_session=True
                )

                if result["status"] in ["active", "trialing"]:
                    activate_user(user, plan_type, result, db)
                    success_count += 1
                else:
                    # Should not happen with error_if_incomplete, but handle gracefully
                    logger.warning(
                        f"⚠️ Unexpected subscription status '{result['status']}' "
                        f"for user {user.id}. Sub: {result['subscription_id']}"
                    )
                    failed_count += 1

            except stripe.error.CardError as e:
                db.rollback()
                err_code = getattr(e, 'code', 'unknown')
                decline_code = getattr(e, 'decline_code', None)

                # Stripe returns different error codes depending on API version and context.
                # All of these mean the same thing: card needs interactive 3DS authentication.
                # "subscription_payment_intent_requires_action" is the most common one when
                # using error_if_incomplete with a card that has mandatory 3DS (SCA banks,
                # Nigerian cards, European regulated cards, Stripe test card 4000 0025 0000 3155).
                REQUIRES_ACTION_CODES = {
                    "authentication_required",
                    "subscription_payment_intent_requires_action",
                    "payment_intent_authentication_failure",
                }

                if err_code in REQUIRES_ACTION_CODES or decline_code in REQUIRES_ACTION_CODES:
                    # Card requires interactive 3DS — cannot complete server-side.
                    # This is NOT a script failure or a card decline — it's a bank requirement.
                    # The user must log in and complete payment manually via the billing page.
                    logger.warning(
                        f"🔐 [3DS REQUIRED] User {user.id} ({user.email}) must complete "
                        f"payment manually. Code: {err_code}"
                    )
                    notify_3ds_required(user, db)
                    skipped_3ds_count += 1
                else:
                    # Genuine card decline: insufficient funds, expired card, stolen card, etc.
                    user_msg = getattr(e, 'user_message', str(e))
                    logger.error(
                        f"❌ Card declined for user {user.id} ({user.email}): "
                        f"{user_msg} (code: {err_code}, decline_code: {decline_code})"
                    )
                    # Notify user their card failed
                    try:
                        from api.services.notification_service import NotificationService
                        NotificationService.create_notification(
                            db=db,
                            user_id=user.id,
                            type="payment_failed",
                            title="Payment Failed",
                            message=(
                                f"We were unable to charge your saved card. "
                                f"Please update your payment method to continue."
                            ),
                            link="/dashboard/upgrade"
                        )
                        db.commit()
                    except Exception:
                        pass
                    failed_count += 1

            except stripe.error.InvalidRequestError as e:
                db.rollback()
                logger.error(f"❌ Stripe invalid request for user {user.id}: {str(e)}")
                # Common cause: customer deleted in Stripe but still in DB
                # or payment method detached. Flag for manual review.
                failed_count += 1

            except Exception as e:
                db.rollback()
                logger.error(f"❌ Unexpected error for user {user.id} ({user.email}): {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                failed_count += 1

        logger.info(
            f"[CRON] Beta billing run complete. "
            f"✅ Success: {success_count} | "
            f"🔐 3DS required (manual): {skipped_3ds_count} | "
            f"❌ Failed: {failed_count}"
        )

    except Exception as e:
        logger.error(f"❌ Fatal error in beta billing process: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    process_beta_billing()