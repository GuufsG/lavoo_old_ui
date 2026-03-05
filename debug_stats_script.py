import os
import sys
from sqlalchemy.orm import Session
from sqlalchemy import func

# Add project root to path
sys.path.append(os.getcwd())

from db.pg_connections import SessionLocal
from db.pg_models import User, BusinessAnalysis, Commission

def debug_stats():
    db = SessionLocal()
    with open("debug_results.log", "w") as f:
        try:
            # Check for Ebube
            f.write("\n=== EBUBE DEBUG ===\n")
            # Find all users matching 'ebube'
            ebube_users = db.query(User).filter(User.name.ilike('%ebube%')).all()
            f.write(f"Users with 'ebube' in name: {len(ebube_users)}\n")
            for u in ebube_users:
                 f.write(f"  User: ID={u.id}, Name='{u.name}', Email='{u.email}'\n")

            if not ebube_users:
                f.write("  No user found with name 'ebube'\n")
                
            # Check Analysis records
            f.write("\n--- BusinessAnalysis Records (Last 10) ---\n")
            analyses = db.query(BusinessAnalysis).limit(10).all()
            for a in analyses:
                user = db.query(User).filter(User.id == a.user_id).first()
                user_name = user.name if user else "UNKNOWN"
                f.write(f"  Analysis ID={a.id}, UserID={a.user_id} ({user_name}), Goal='{a.business_goal[:20]}...'\n")

            # Check for Tony
            f.write("\n=== TONY DEBUG ===\n")
            tony = db.query(User).filter(User.email == 'tony@gmail.com').first()
            if tony:
                f.write(f"Found User: ID={tony.id}, Name='{tony.name}', Email='{tony.email}'\n")
                
                commissions = db.query(Commission).filter(Commission.user_id == tony.id).all()
                f.write(f"Commissions for User ID {tony.id}: {len(commissions)}\n")
                for c in commissions:
                    f.write(f"  Comm ID={c.id}, Amount={c.amount}, Status='{c.status}', OriginalAmount={c.original_amount}\n")
            else:
                f.write("User 'tony@gmail.com' not found.\n")
                
            # Check all commissions to see who owns them
            f.write("\n--- All Commission Records (Last 10) ---\n")
            all_commissions = db.query(Commission).limit(10).all()
            for c in all_commissions:
                 user = db.query(User).filter(User.id == c.user_id).first()
                 user_name = user.name if user else "UNKNOWN"
                 f.write(f"  Comm ID={c.id}, UserID={c.user_id} ({user_name}), Amount={c.amount}, Status='{c.status}'\n")

        finally:
            db.close()

if __name__ == "__main__":
    debug_stats()
