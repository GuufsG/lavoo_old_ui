
import os
import sys
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from db.pg_connections import SessionLocal
from db.pg_models import User, Subscriptions

def reset_all_active_users():
    db = SessionLocal()
    try:
        # Find all users with active status
        users = db.query(User).filter(User.subscription_status == "active").all()
        print(f"Found {len(users)} active users to reset")
        
        for user in users:
            print(f"Resetting user: {user.email}")
            user.subscription_status = "Free"
            user.subscription_plan = None
            user.stripe_payment_method_id = None
            user.card_last4 = None
            user.card_brand = None
            user.subscription_expires_at = None
            
            # ALSO deactivate subscription records in the subscriptions table
            # This is CRITICAL because sub_utils.sync_user_subscription uses this table as source of truth
            active_subs = db.query(Subscriptions).filter(
                Subscriptions.user_id == user.id,
                Subscriptions.subscription_status == "active"
            ).all()
            
            for sub in active_subs:
                print(f"  - Deactivating subscription record: {sub.transaction_id}")
                sub.subscription_status = "expired"
                sub.status = "reset" # Mark as reset to differentiate from organic expiration
            
            # Optional: reset grace period if testing new signup flow
            # user.grace_period_ends_at = None
            
        db.commit()
        print("✅ All active users reset successfully")
    except Exception as e:
        db.rollback()
        print(f"❌ Error resetting users: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    load_dotenv()
    reset_all_active_users()
