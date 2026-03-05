
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import os
from sqlalchemy.orm import Session
from db.pg_models import User
from dotenv import dotenv_values

class BetaService:
    
    @staticmethod
    def get_app_mode() -> str:
        """
        Get current application mode: 'beta', 'launch', or 'development'
        Reads dynamically from .env to pick up changes without restarting,
        fallbacks to os.getenv.
        """
        env_vars = dotenv_values(".env")
        
        mode = env_vars.get("APP_MODE")
        if mode is None:
            mode = os.getenv("APP_MODE", "")
            
        mode = str(mode).lower().strip()
        if mode in ["beta", "launch", "development"]:
            return mode
            
        # Legacy support / Fallback
        legacy_beta = env_vars.get("BETA_MODE")
        if legacy_beta is None:
            legacy_beta = os.getenv("BETA_MODE", "false")
            
        if str(legacy_beta).lower().strip() == "true":
            return "beta"
            
        return "launch"

    @staticmethod
    def is_beta_mode() -> bool:
        """Check if system is in beta mode (or launch mode before launch date)"""
        mode = BetaService.get_app_mode()
        if mode == "beta":
            return True
            
        if mode == "launch":
            launch_date = BetaService.get_launch_date()
            if launch_date:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if now < launch_date:
                    return True
                    
        return False
    
    @staticmethod
    def get_launch_date() -> Optional[datetime]:
        """Get configured launch date (supports DD/MM/YYYY)"""
        env_vars = dotenv_values(".env")
        launch_str = env_vars.get("LAUNCH_DATE") or os.getenv("LAUNCH_DATE")
        if launch_str:
            try:
                # Try DD/MM/YYYY first as requested by user
                return datetime.strptime(launch_str, "%d/%m/%Y")
            except ValueError:
                try:
                    # Fallback to ISO format
                    return datetime.strptime(launch_str, "%Y-%m-%d")
                except ValueError:
                    return None
        return None
    
    @staticmethod
    def get_grace_period_days() -> int:
        """Get grace period duration (default: 5 days)"""
        env_vars = dotenv_values(".env")
        days = env_vars.get("GRACE_PERIOD_DAYS")
        if days is None:
            days = os.getenv("GRACE_PERIOD_DAYS", "5")
        return int(days)
    
    @staticmethod
    def calculate_grace_period_end(launch_date: datetime) -> datetime:
        """Calculate when grace period ends"""
        grace_days = BetaService.get_grace_period_days()
        return launch_date + timedelta(days=grace_days)
    
    @staticmethod
    def is_in_grace_period(user: Optional[User] = None) -> bool:
        """
        Check if we're currently in grace period.
        If user is provided, checks their specific grace period.
        """
        # If in beta, we are NOT in grace period yet
        if BetaService.is_beta_mode():
            return False
        
        now = datetime.now(timezone.utc).replace(tzinfo=None) # Standardize to naive UTC

        # Check user-specific grace period first
        if user and user.grace_period_ends_at:
            grace_end = user.grace_period_ends_at
            if getattr(user, 'is_beta_user', False):
                launch_date = BetaService.get_launch_date()
                if launch_date:
                    grace_end = BetaService.calculate_grace_period_end(launch_date)
                    
            if now < grace_end:
                return True
        
        # Fallback to global launch date
        launch_date = BetaService.get_launch_date()
        if launch_date:
            # If we have a launch date and it hasn't happened yet, we aren't in grace
            if now < launch_date:
                return False
            
            grace_end = BetaService.calculate_grace_period_end(launch_date)
            if now < grace_end:
                return True
                
        return False
    
    @staticmethod
    def has_saved_card(user: User) -> bool:
        """Check if user has a saved payment method"""
        return bool(user.stripe_payment_method_id)
    
    @staticmethod
    def get_user_status(user: User) -> Dict:
        """
        Get comprehensive user status for dashboard display
        
        Returns dict with:
        - status: 'beta_no_card', 'beta_with_card', 'grace_no_card', 'grace_with_card', 'active', 'new_user'
        - message: Display message
        - action_required: Boolean
        - countdown_ends_at: Datetime or None
        - days_remaining: Integer or None
        """
        launch_date = BetaService.get_launch_date()
        has_card = BetaService.has_saved_card(user)
        
        # BETA PERIOD (Before Launch)
        if BetaService.is_beta_mode():
            if has_card:
                # Card saved in beta mode - banner hides, billing happens at launch
                app_mode = BetaService.get_app_mode()
                return {
                    "status": "beta_with_card",
                    "message": "You're all set! Your card is saved and will be billed at launch.",
                    "action_required": False,
                    "countdown_ends_at": launch_date if app_mode == "launch" else None,
                    "days_remaining": None,
                    "show_card_info": True,
                    "is_beta_user": True
                }
            else:
                app_mode = BetaService.get_app_mode()
                return {
                    "status": "beta_no_card",
                    "message": "Save your card now to secure your access after the beta period!",
                    "action_required": True,
                    "countdown_ends_at": launch_date if app_mode == "launch" else None,
                    "days_remaining": None,
                    "show_card_info": False,
                    "is_beta_user": True
                }
        
        # GRACE PERIOD (Launch Day + 5 Days OR Signup Day + 5 Days)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        if BetaService.is_in_grace_period(user):
            grace_end = user.grace_period_ends_at
            is_beta = getattr(user, 'is_beta_user', False)
            
            # If user has no personal grace end, or if they are a beta user (to ensure .env changes are instantly reflected),
            # we should calculate it dynamically.
            if not grace_end or is_beta:
                 launch_date = BetaService.get_launch_date()
                 if launch_date:
                     grace_end = BetaService.calculate_grace_period_end(launch_date)

            if not grace_end:
                 app_mode = BetaService.get_app_mode()
                 launch_date = BetaService.get_launch_date()
                 return {
                    "status": "new_user",
                    "message": "Subscribe to get started with Lavoo",
                    "action_required": True,
                    "countdown_ends_at": launch_date if app_mode == "launch" else None,
                    "days_remaining": None,
                    "show_card_info": False
                }

            time_remaining = grace_end - now
            days_rem = time_remaining.days
            
            is_beta = getattr(user, 'is_beta_user', False)
            if has_card:
                # Card saved - banner hides (billing is immediate in launch mode → user becomes active)
                return {
                    "status": "grace_with_card",
                    "message": "",
                    "action_required": False,
                    "countdown_ends_at": None,
                    "days_remaining": None,
                    "show_card_info": True,
                    "is_beta_user": is_beta
                }
            else:
                # No card saved — show the notice with countdown
                message = (
                    "Save your card now to secure your access after the beta period!"
                    if is_beta
                    else "Subscribe now to keep your access to Lavoo!"
                )
                return {
                    "status": "grace_no_card",
                    "message": message,
                    "action_required": True,
                    "countdown_ends_at": grace_end,
                    "days_remaining": days_rem,
                    "hours_remaining": (time_remaining.seconds // 3600),
                    "minutes_remaining": (time_remaining.seconds % 3600) // 60,
                    "seconds_remaining": (time_remaining.seconds % 60),
                    "show_card_info": False,
                    "is_beta_user": is_beta
                }
        
        # AFTER GRACE PERIOD OR ACTIVE SUBSCRIPTION
        # Check active subscription FIRST — if user paid, no timer needed regardless of card on file
        if user.subscription_status == "active":
            days_rem = None
            if user.subscription_expires_at:
                 expires_naive = user.subscription_expires_at.replace(tzinfo=None) if user.subscription_expires_at.tzinfo else user.subscription_expires_at
                 days_rem = (expires_naive - now).days

            return {
                "status": "active",
                "message": "Your subscription is active",
                "action_required": False,
                "countdown_ends_at": user.subscription_expires_at,
                "days_remaining": days_rem,
                "show_card_info": True
            }
        
        # Pause access if grace period is over and no card/active sub
        if user.grace_period_ends_at:
             grace_naive = user.grace_period_ends_at.replace(tzinfo=None) if user.grace_period_ends_at.tzinfo else user.grace_period_ends_at
             if now >= grace_naive:
                 return {
                    "status": "grace_expired_no_card",
                    "message": "Your access is paused. Add a payment method to reactivate.",
                    "action_required": True,
                    "countdown_ends_at": None,
                    "days_remaining": None,
                    "show_card_info": False
                }

        # NEW USER (No Grace Period set yet)
        app_mode = BetaService.get_app_mode()
        launch_date = BetaService.get_launch_date()
        return {
            "status": "new_user",
            "message": "Subscribe to get started with Lavoo",
            "action_required": True,
            "countdown_ends_at": launch_date if app_mode == "launch" else None,
            "days_remaining": None,
            "show_card_info": False
        }
    
    @staticmethod
    def initialize_grace_period(user: User, db: Session):
        """
        Initialize grace period based on user type:
        - Beta users: 5 days from launch date
        - New users: 5 days from signup date
        """
        is_beta = BetaService.is_beta_mode()
        launch_date = BetaService.get_launch_date()
        grace_days = BetaService.get_grace_period_days()
        
        if is_beta:
            # Beta user: Grace period starts at launch
            user.is_beta_user = True
            user.beta_joined_at = datetime.utcnow()
            if launch_date:
                user.grace_period_ends_at = launch_date + timedelta(days=grace_days)
        else:
            # Post-launch user: Grace period starts now
            user.is_beta_user = False
            user.grace_period_ends_at = datetime.utcnow() + timedelta(days=grace_days)
            
        db.add(user)
        db.flush()

    @staticmethod
    def mark_as_beta_user(user: User, db: Session):
        """Mark user as beta participant (force beta status)"""
        user.is_beta_user = True
        user.beta_joined_at = datetime.utcnow()
        
        launch_date = BetaService.get_launch_date()
        if launch_date:
            user.grace_period_ends_at = BetaService.calculate_grace_period_end(launch_date)
        
        db.flush()
    
    @staticmethod
    def should_send_reminder(user: User, notification_type: str, db: Session) -> bool:
        """
        Check if we should send a reminder (prevent spam)
        Returns True if:
        - No notification of this type sent in last 24 hours
        - User hasn't completed the action
        """
        from db.pg_models import NotificationHistory
        
        # Check if notification was sent recently
        cutoff = datetime.utcnow() - timedelta(hours=24)
        recent = db.query(NotificationHistory).filter(
            NotificationHistory.user_id == user.id,
            NotificationHistory.notification_type == notification_type,
            NotificationHistory.sent_at >= cutoff
        ).first()
        
        if recent:
            return False
        
        # Check if action is still needed
        if notification_type == "beta_card_reminder":
            return not BetaService.has_saved_card(user)
        elif notification_type == "grace_period_warning":
            return not BetaService.has_saved_card(user) and BetaService.is_in_grace_period()
        
        return False