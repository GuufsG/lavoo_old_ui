
import os
import sys
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# Add current directory to path
sys.path.append(os.getcwd())

from db.pg_connections import get_db
from db.pg_models import User
from subscriptions.beta_service import BetaService

def verify_user_flow(email: str):
    db = next(get_db())
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        print(f"❌ User {email} not found")
        return

    mode = BetaService.get_app_mode()
    status = BetaService.get_user_status(user)
    
    print(f"\n--- Flow Verification for {email} ---")
    print(f"Current APP_MODE: {mode.upper()}")
    print(f"User is_beta_user: {user.is_beta_user}")
    print(f"User subscription_status: {user.subscription_status}")
    print(f"User grace_period_ends_at: {user.grace_period_ends_at}")
    print(f"Calculated status: {status['status']}")
    print(f"Display message: {status['message']}")
    
    if mode == "launch" and user.is_beta_user and user.stripe_payment_method_id:
        print("💡 TIP: This user is a Beta user with a saved card in Launch mode.")
        print("   Running 'python cron/process_beta_billing.py' should charge them immediately.")
    
    if mode == "launch" and not user.is_beta_user and not user.stripe_payment_method_id:
        print("💡 TIP: This user is a Launch signup. They have 5 days from signup to save their card.")
        if user.grace_period_ends_at:
            remaining = (user.grace_period_ends_at - datetime.utcnow()).days
            print(f"   Days remaining in grace period: {remaining}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python simulate_flow.py <user_email>")
    else:
        verify_user_flow(sys.argv[1])
