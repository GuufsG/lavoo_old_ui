import os
import sys
from sqlalchemy.orm import Session
from sqlalchemy import func
from decimal import Decimal

# Add project root to path
sys.path.append(os.getcwd())

from db.pg_connections import SessionLocal
from db.pg_models import User, BusinessAnalysis, Commission, Referral

def verify_backend_logic():
    db = SessionLocal()
    with open("backend_verification.log", "w") as f:
        try:
            # --- EBUBE (ID 18) ---
            user_id = 18
            f.write(f"\n=== TESTING EBUBE (ID {user_id}) ===\n")
            
            # Exact logic from user_stats.py
            total_analyses = db.query(func.count(BusinessAnalysis.id)).filter(
                BusinessAnalysis.user_id == user_id
            ).scalar() or 0
            f.write(f"Query: count(BusinessAnalysis) where user_id={user_id}\n")
            f.write(f"Result: {total_analyses}\n")

            # --- TONY (ID 16) ---
            user_id = 16
            f.write(f"\n=== TESTING TONY (ID {user_id}) ===\n")
            
            # Exact logic from user_stats.py
            # Sum 'paid', 'pending', 'processing', 'approved'
            total_commissions = db.query(func.sum(Commission.amount)).filter(
                Commission.user_id == user_id,
                Commission.status.in_(['paid', 'pending', 'processing', 'approved']) 
            ).scalar() or 0.0
            
            f.write(f"Query: sum(Commission.amount) where user_id={user_id} and status in [...]\n")
            f.write(f"Result: {total_commissions} (Type: {type(total_commissions)})\n")

            paid_commissions = db.query(func.sum(Commission.amount)).filter(
                Commission.user_id == user_id,
                Commission.status == 'paid'
            ).scalar() or 0.0
            f.write(f"Query: sum(Commission.amount) where user_id={user_id} and status='paid'\n")
            f.write(f"Result: {paid_commissions}\n")

            # Referral Stats Logic
            # Method A: Use the synced count from User table
            user_obj = db.query(User).filter(User.id == user_id).first()
            total_referrals = user_obj.referral_count or 0
            f.write(f"User.referral_count: {total_referrals}\n")

        except Exception as e:
            f.write(f"EXCEPTION: {e}\n")
            import traceback
            f.write(traceback.format_exc())
            
        finally:
            db.close()

if __name__ == "__main__":
    verify_backend_logic()
