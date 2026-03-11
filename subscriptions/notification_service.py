from datetime import datetime
from sqlalchemy.orm import Session
from db.pg_models import User, UserNotification, NotificationHistory


class NotificationService:

    @staticmethod
    def create_notification(
        db: Session,
        user_id: int,
        title: str,
        message: str,
        link: str = None,
        # Accept both spellings — stripe.py uses type=, other code uses notification_type=
        notification_type: str = None,
        type: str = None,
    ):
        """
        Create an in-app notification.

        The `type` parameter is accepted as an alias for `notification_type`
        so that callers using either keyword work correctly.
        Notifications persist until the user explicitly reads them.
        """
        resolved_type = notification_type or type or "general"

        notification = UserNotification(
            user_id=user_id,
            type=resolved_type,
            title=title,
            message=message,
            link=link,
            is_read=False,          # Stays visible until user reads it
            created_at=datetime.utcnow()
        )
        db.add(notification)

        # Track that the notification was sent
        history = NotificationHistory(
            user_id=user_id,
            notification_type=resolved_type,
            sent_at=datetime.utcnow()
        )
        db.add(history)

        db.commit()
        return notification

    @staticmethod
    def mark_as_read(db: Session, notification_id: int, user_id: int) -> bool:
        """
        Mark a single notification as read. Returns True if found and updated.
        Call this from the frontend when the user clicks or views the notification.
        """
        notification = db.query(UserNotification).filter(
            UserNotification.id == notification_id,
            UserNotification.user_id == user_id
        ).first()

        if not notification:
            return False

        notification.is_read = True
        notification.read_at = datetime.utcnow()
        db.commit()
        return True

    @staticmethod
    def mark_all_as_read(db: Session, user_id: int) -> int:
        """
        Mark all unread notifications as read for a user.
        Returns the number of notifications updated.
        """
        updated = db.query(UserNotification).filter(
            UserNotification.user_id == user_id,
            UserNotification.is_read == False
        ).all()

        now = datetime.utcnow()
        for n in updated:
            n.is_read = True
            n.read_at = now

        db.commit()
        return len(updated)

    @staticmethod
    def get_unread(db: Session, user_id: int) -> list:
        """Return all unread notifications for a user, newest first."""
        return (
            db.query(UserNotification)
            .filter(
                UserNotification.user_id == user_id,
                UserNotification.is_read == False
            )
            .order_by(UserNotification.created_at.desc())
            .all()
        )

    @staticmethod
    def send_beta_card_reminder(db: Session, user: User):
        """Send reminder to save card during beta."""
        from api.services.beta_service import BetaService

        if not BetaService.should_send_reminder(user, "beta_card_reminder", db):
            return None

        return NotificationService.create_notification(
            db=db,
            user_id=user.id,
            notification_type="beta_card_reminder",
            title="💳 Save Your Card for Launch Access",
            message="Secure your access today! Save your card now to ensure uninterrupted access on launch day.",
            link="/dashboard/upgrade"
        )

    @staticmethod
    def send_grace_period_warning(db: Session, user: User):
        """Send warning during grace period."""
        from api.services.beta_service import BetaService

        if not BetaService.should_send_reminder(user, "grace_period_warning", db):
            return None

        if user.grace_period_ends_at:
            days_remaining = (user.grace_period_ends_at - datetime.utcnow()).days
        else:
            days_remaining = BetaService.get_grace_period_days()

        return NotificationService.create_notification(
            db=db,
            user_id=user.id,
            notification_type="grace_period_warning",
            title="⏰ Action Required - Add Payment Method",
            message=f"You have {days_remaining} days left to add a payment method. After this, your access will be paused.",
            link="/dashboard/upgrade"
        )

    @staticmethod
    def send_card_saved_success(db: Session, user: User):
        """Notify user that card was saved successfully."""
        from .beta_service import BetaService

        if BetaService.is_beta_mode():
            message = "Congratulations on becoming a part of the Lavoo Community! Your card is securely saved for launch."
        else:
            message = "Congratulations on becoming a part of the Lavoo Community! Your subscription is now active."

        return NotificationService.create_notification(
            db=db,
            user_id=user.id,
            notification_type="card_saved",
            title="✅ Card Saved Successfully",
            message=message,
            link="/dashboard"
        )