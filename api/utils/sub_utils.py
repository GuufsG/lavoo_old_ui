from datetime import datetime, timezone
import logging
from sqlalchemy.orm import Session
from db.pg_models import User, Subscriptions

logger = logging.getLogger(__name__)


def sync_user_subscription(db: Session, user: User) -> User:
    """
    Syncs user.subscription_status and user.subscription_plan against the
    Subscriptions table. Called on every /api/me and /api/stripe/beta/status.

    DECISION TREE
    ─────────────
    1. Is there a completed, active, non-expired Subscriptions DB record?
       YES → status = "active"   NO → go to 2

    2. Is there any Subscriptions record in an in-flight 3DS state
       (incomplete/pending/requires_action)?
       YES → skip sync (3DS is in progress)   NO → go to 3

    3. Are we in beta mode OR is the user still within their grace period?
       YES → keep/set status = "active"   NO → set status = "Free"

    NOTE ON stripe_subscription_id:
    We do NOT use the stripe_subscription_id field as a "mid-checkout guard"
    here. That field is stale after failed or incomplete payment attempts —
    the previous version used it as a guard, which caused the sync to skip
    even after the user's old subscription had already been cancelled and
    they needed to re-subscribe. Instead we look directly at the Subscriptions
    table for in-flight records.
    """
    try:
        # ── 1. Check for a valid active subscription record ───────────────────
        latest_sub = db.query(Subscriptions).filter(
            Subscriptions.user_id == user.id,
            Subscriptions.status.in_(('completed', 'active', 'paid', 'successful', 'succeeded', 'trialing')),
            ~Subscriptions.subscription_status.in_(
                ('incomplete', 'pending', 'requires_action', 'requires_payment_method', 'Free')
            )
        ).order_by(Subscriptions.id.desc()).first()

        if latest_sub:
            if latest_sub.end_date and latest_sub.end_date.tzinfo is None:
                end_date = latest_sub.end_date.replace(tzinfo=timezone.utc)
            else:
                end_date = latest_sub.end_date

            now = datetime.now(end_date.tzinfo if end_date else timezone.utc)
            is_expired = end_date and end_date < now

            new_sub_status = "Free" if is_expired else "active"
            new_user_status = "Free" if is_expired else "active"
            new_plan = latest_sub.subscription_plan if not is_expired else None

            if getattr(latest_sub, 'subscription_status', None) != new_sub_status:
                try:
                    latest_sub.subscription_status = new_sub_status
                    db.add(latest_sub)
                except Exception as attr_err:
                    logger.warning(f"Could not update sub record {latest_sub.id}: {attr_err}")

            if user.subscription_status != new_user_status or user.subscription_plan != new_plan:
                user.subscription_status = new_user_status
                user.subscription_plan = new_plan
                db.add(user)
                logger.info(f"🔄 {user.email}: synced → {new_user_status} ({new_plan})")

            db.commit()
            return user

        # ── 2. Check for in-flight 3DS record ────────────────────────────────
        # If there is an incomplete/pending Subscriptions record, 3DS is in
        # progress. Do not touch the user's status.
        in_flight = db.query(Subscriptions).filter(
            Subscriptions.user_id == user.id,
            Subscriptions.subscription_status.in_(
                ('incomplete', 'pending', 'requires_action', 'requires_payment_method')
            )
        ).first()

        if in_flight:
            logger.info(
                f"⏳ {user.email}: in-flight 3DS record found "
                f"(sub record id={in_flight.id}, status='{in_flight.subscription_status}') — skipping sync"
            )
            return user

        # ── 3. No subscription found — apply beta/grace/Free logic ────────────
        if user.subscription_status == "Free":
            return user  # Already Free

        from subscriptions.beta_service import BetaService

        if BetaService.is_beta_mode():
            if user.subscription_status != "active":
                user.subscription_status = "active"
                db.add(user)
                db.commit()
                logger.info(f"✅ {user.email}: kept active (beta mode)")
            return user

        if BetaService.is_in_grace_period(user):
            if user.subscription_status != "active":
                user.subscription_status = "active"
                db.add(user)
                db.commit()
                logger.info(f"✅ {user.email}: kept active (grace period, ends {user.grace_period_ends_at})")
            return user

        # Grace period expired, no subscription → downgrade
        user.subscription_status = "Free"
        user.subscription_plan = None
        db.add(user)
        db.commit()
        logger.info(f"🔒 {user.email}: downgraded to Free (grace period expired, no subscription)")

    except Exception as e:
        logger.error(f"❌ sync_user_subscription error for {user.email}: {e}")
        db.rollback()

    return user