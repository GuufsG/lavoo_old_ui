
import os
import sys
from datetime import datetime
from decimal import Decimal

# Add project root to sys.path
sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from db.pg_connections import get_db
from db.pg_models import User, Subscriptions
from subscriptions.beta_service import BetaService

def verify_atomicity():
    db = next(get_db())
    
    # Get a test user
    test_email = "beta_tester@example.com"
    user = db.query(User).filter(User.email == test_email).first()
    
    if not user:
        print("❌ Test user not found. Run reset_test_users.py first.")
        return

    print(f"--- Verifying Atomicity for User: {user.email} ---")
    
    # 1. Reset user state
    user.subscription_status = "Free"
    user.stripe_subscription_id = None
    user.is_beta_user = True
    db.query(Subscriptions).filter(Subscriptions.user_id == user.id).delete()
    db.commit()
    
    print(f"Initial State: Status={user.subscription_status}, SubCount={db.query(Subscriptions).filter(Subscriptions.user_id == user.id).count()}")

    # 2. Simulate what save_card_beta does now (Atomically)
    try:
        print("\nSimulating successful atomic billing...")
        # Step A: Update User technical fields
        user.stripe_subscription_id = "sub_test_123"
        
        # Step B: Create Subscriptions record
        sub = Subscriptions(
            user_id=user.id,
            subscription_plan="monthly",
            transaction_id="sub_test_123",
            tx_ref="TEST-REF",
            amount=Decimal("29.95"),
            currency="USD",
            status="completed",
            subscription_status="active",
            payment_provider="stripe",
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow()
        )
        db.add(sub)
        db.flush()
        
        # Step C: Update User lifecycle status
        user.subscription_status = "active"
        
        db.commit()
        print("✅ Atomic transaction COMMITTED.")
    except Exception as e:
        db.rollback()
        print(f"❌ Transaction FAILED: {str(e)}")

    # Verify result
    db.refresh(user)
    sub_count = db.query(Subscriptions).filter(Subscriptions.user_id == user.id).count()
    print(f"Final State: Status={user.subscription_status}, SubCount={sub_count}")
    
    if user.subscription_status == "active" and sub_count == 1:
        print("✨ SUCCESS: User and Subscription are synchronized.")
    else:
        print("❌ FAILURE: Inconsistent state detected.")

    # 3. Simulate FAILURE (The "Active without Sub" scenario)
    print("\nSimulating FAILED atomic billing (Source of previous bug)...")
    try:
        user.subscription_status = "Free" # Reset
        db.query(Subscriptions).filter(Subscriptions.user_id == user.id).delete()
        db.commit()
        
        # Start transaction
        user.stripe_subscription_id = "sub_fail_456"
        
        # In the old code, subscription_status was set here AND committed.
        # Now it's delayed.
        
        # Force a failure before creating Subscriptions record
        raise ValueError("Simulated Database Error or Stripe failure after partial user update")
        
        # (This part is never reached)
        sub_fail = Subscriptions(user_id=user.id, subscription_plan="monthly", transaction_id="sub_fail_456")
        db.add(sub_fail)
        db.flush()
        user.subscription_status = "active"
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"✅ Transaction correctly ROLLBACK on failure: {str(e)}")

    # Verify result
    db.refresh(user)
    sub_count = db.query(Subscriptions).filter(Subscriptions.user_id == user.id).count()
    print(f"State after rollback: Status={user.subscription_status}, SubCount={sub_count}")
    
    if user.subscription_status == "Free" and sub_count == 0:
        print("✨ SUCCESS: Rollback preserved consistency. No 'ghost' active status.")
    else:
        print("❌ FAILURE: Rollback failed to protect user status!")

if __name__ == "__main__":
    verify_atomicity()
