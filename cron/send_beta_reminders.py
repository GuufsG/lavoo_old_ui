
"""
Send reminder notifications to beta users
Run daily via cron: 0 9 * * * (9 AM every day)
"""

import os
import sys
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from db.pg_connections import get_db
from db.pg_models import User
from subscriptions.beta_service import BetaService
from subscriptions.notification_service import NotificationService
from config.logging import get_logger, setup_logging

# Initialize Logging
setup_logging()
logger = get_logger(__name__)

def send_beta_reminders():
    """Send reminders to beta users who haven't saved cards"""
    db = next(get_db())
    
    try:
        # BETA PERIOD: Remind users to save cards
        if BetaService.is_beta_mode():
            users_without_cards = db.query(User).filter(
                User.is_beta_user == True,
                User.stripe_payment_method_id.is_(None)
            ).all()
            
            print(f"Found {len(users_without_cards)} beta users without cards")
            
            for user in users_without_cards:
                NotificationService.send_beta_card_reminder(db, user)
        
        # GRACE PERIOD: Warn users to save cards
        elif BetaService.is_in_grace_period():
            users_without_cards = db.query(User).filter(
                User.is_beta_user == True,
                User.stripe_payment_method_id.is_(None)
            ).all()
            
            print(f"Found {len(users_without_cards)} users in grace period without cards")
            
            for user in users_without_cards:
                NotificationService.send_grace_period_warning(db, user)
        
        print("✅ Reminders sent successfully")
        
    except Exception as e:
        print(f"❌ Error sending reminders: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    send_beta_reminders()