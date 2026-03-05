
from datetime import datetime
from sqlalchemy.orm import Session
from db.pg_models import User, UserNotification, NotificationHistory

class NotificationService:
    
    @staticmethod
    def create_notification(
        db: Session,
        user_id: int,
        notification_type: str,
        title: str,
        message: str,
        link: str = None
    ):
        """Create in-app notification"""
        notification = UserNotification(
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            link=link,
            created_at=datetime.utcnow()
        )
        
        db.add(notification)
        
        # Track notification sent
        history = NotificationHistory(
            user_id=user_id,
            notification_type=notification_type,
            sent_at=datetime.utcnow()
        )
        db.add(history)
        
        db.commit()
        return notification
    
    @staticmethod
    def send_beta_card_reminder(db: Session, user: User):
        """Send reminder to save card during beta"""
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
        """Send warning during grace period"""
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
        """Notify user that card was saved successfully"""
        from .beta_service import BetaService
        
        launch_date = BetaService.get_launch_date()
        grace_end = user.grace_period_ends_at
        
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