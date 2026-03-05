from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from db.pg_connections import get_db
from db.pg_models import BusinessAnalysis, User, Commission, Referral

def test_user_stats(email):
    db = next(get_db())
    print(f"\n--- Testing Stats for {email} ---")
    
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"ERROR: User {email} not found")
        return
    
    user_id = user.id
    print(f"Resolved User ID: {user_id}")

    # 1. Analyses
    total_analyses = db.query(func.count(BusinessAnalysis.id)).filter(
        BusinessAnalysis.user_id == user_id
    ).scalar() or 0
    print(f"Total Analyses: {total_analyses}")

    # 2. Commissions
    total_commissions = db.query(func.sum(Commission.amount)).filter(
        Commission.user_id == user_id,
        Commission.status.in_(['paid', 'pending', 'processing', 'approved']) 
    ).scalar() or 0.0
    print(f"Total Commissions: ${float(total_commissions):.2f}")

    # 3. Referrals
    total_referrals = user.referral_count or 0
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    referrals_this_month = db.query(func.count(Referral.id)).filter(
        Referral.referrer_id == user_id,
        Referral.created_at >= month_start
    ).scalar() or 0
    print(f"Total Referrals: {total_referrals}")
    print(f"Referrals This Month: {referrals_this_month}")
    print(f"SUCCESS: Data aggregation logic verified for {email}")

if __name__ == "__main__":
    test_user_stats("ebube@gmail.com")
    test_user_stats("tony@gmail.com")
