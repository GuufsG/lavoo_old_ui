
import os
import sys
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

load_dotenv()

def verify_stripe_env():
    print("--- Verifying Stripe Environment ---")
    monthly = os.getenv("STRIPE_MONTHLY_PRICE_ID")
    yearly = os.getenv("STRIPE_YEARLY_PRICE_ID")
    print(f"STRIPE_MONTHLY_PRICE_ID: {monthly}")
    print(f"STRIPE_YEARLY_PRICE_ID: {yearly}")
    
    from subscriptions.stripe import get_stripe_price_id
    print(f"get_stripe_price_id('monthly'): {get_stripe_price_id('monthly')}")
    print(f"get_stripe_price_id('yearly'): {get_stripe_price_id('yearly')}")
    
    if get_stripe_price_id('monthly') == monthly and monthly:
        print("✅ Stripe Price ID resolution works!")
    else:
        print("❌ Stripe Price ID resolution failed or env var missing.")

def verify_db_notifications():
    print("\n--- Verifying Database Notifications ---")
    from db.pg_connections import SessionLocal
    from db.pg_models import UserNotification, User, NotificationType
    from api.services.notification_service import NotificationService
    
    db = SessionLocal()
    try:
        # Get a test user
        user = db.query(User).first()
        if not user:
            print("❌ No users found in DB to test with.")
            return
        
        print(f"Testing with user: {user.email} (ID: {user.id})")
        
        # Create a test notification
        notif = NotificationService.create_notification(
            db=db,
            user_id=user.id,
            type=NotificationType.SYSTEM_ALERT.value,
            title="Test Notification",
            message="This is a verification test notification."
        )
        
        if notif:
            print(f"✅ Notification created: {notif.id}")
            # Verify retrieval
            db.refresh(notif)
            if notif.title == "Test Notification":
                print("✅ Notification data integrity verified.")
        else:
            print("❌ Notification creation failed.")
            
    except Exception as e:
        print(f"❌ DB Test Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    verify_stripe_env()
    verify_db_notifications()
