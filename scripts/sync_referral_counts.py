"""
Sync referral_count for all users from actual Referral table records.
This ensures user.referral_count matches the actual count in the referrals table.
"""

from db.pg_connections import get_db
from db.pg_models import User, Referral
from sqlalchemy import func

def sync_all_referral_counts():
    """Sync referral_count for all users"""
    db = next(get_db())
    
    try:
        # Get all users
        users = db.query(User).all()
        updated_count = 0
        
        for user in users:
            # Count actual referrals from Referral table
            actual_count = db.query(func.count(Referral.id)).filter(
                Referral.referrer_id == user.id
            ).scalar() or 0
            
            # Update if mismatch
            if user.referral_count != actual_count:
                print(f"User {user.id} ({user.email}): referral_count={user.referral_count} -> {actual_count}")
                user.referral_count = actual_count
                updated_count += 1
        
        db.commit()
        print(f"\n✅ Synced {updated_count} users' referral counts")
        
    except Exception as e:
        print(f"❌ Error syncing referral counts: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("Starting referral count sync...")
    sync_all_referral_counts()
    print("Done!")
