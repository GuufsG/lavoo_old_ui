"""
Diagnostic script to check all stats for a specific user.
Run this to see exactly what is in the database for a user.
"""

import sys
import os

# Add parent directory to path to allow importing from db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, text
from db.pg_connections import get_db
from db.pg_models import User, Referral, BusinessAnalysis, Commission

def diagnose_user(email_or_id):
    db = next(get_db())
    
    try:
        print(f"\n🔍 Diagnosing user: {email_or_id}")
        
        # 1. Find User
        if str(email_or_id).isdigit():
            user = db.query(User).filter(User.id == int(email_or_id)).first()
        else:
            user = db.query(User).filter(User.email == str(email_or_id)).first()
            
        if not user:
            print("❌ User not found!")
            return

        print(f"✅ User Found: {user.name} (ID: {user.id})")
        print(f"   Email: {user.email}")
        print(f"   Stored Referral Count (User.referral_count): {user.referral_count}")
        
        # 2. Check Referrals Table
        actual_referrals = db.query(Referral).filter(Referral.referrer_id == user.id).all()
        print(f"\n👥 Referrals Table Check:")
        print(f"   Count in table: {len(actual_referrals)}")
        for ref in actual_referrals:
            print(f"   - ID: {ref.id}, Created: {ref.created_at}, Chops: {ref.chops_awarded}")
            
        # 3. Check Commissions Table
        commissions = db.query(Commission).filter(Commission.user_id == user.id).all()
        print(f"\n💰 Commissions Table Check:")
        print(f"   Count in table: {len(commissions)}")
        total_comm = 0.0
        for comm in commissions:
            print(f"   - ID: {comm.id}, Amount: ${comm.amount}, Status: {comm.status}")
            total_comm += float(comm.amount)
        print(f"   Calculated Total: ${total_comm}")
        
        # 4. Check Analyses Table
        analyses = db.query(BusinessAnalysis).filter(BusinessAnalysis.user_id == user.id).all()
        print(f"\n📊 Analyses Table Check (BusinessAnalysis):")
        print(f"   Count in table: {len(analyses)}")
        for ana in analyses:
            print(f"   - ID: {ana.id}, Created: {ana.created_at}, Title: {ana.company_name}")

    except Exception as e:
        print(f"❌ Error during diagnosis: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_user_data.py <email_or_user_id>")
    else:
        diagnose_user(sys.argv[1])
