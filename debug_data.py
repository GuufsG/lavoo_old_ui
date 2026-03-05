from db.pg_connections import get_db
from db.pg_models import User, BusinessAnalysis, Commission
import sys

def debug_data():
    with open("debug_results.log", "w", encoding="utf-8") as f:
        db = next(get_db())
        # Search for ebube and tony
        users = db.query(User).filter((User.email.like('%ebube%')) | (User.email == 'tony@gmail.com') | (User.name.like('%ebube%'))).all()
        f.write(f"FOUND_USERS: {len(users)}\n")
        for u in users:
            f.write(f"USER: id={u.id}, email={u.email}, name={u.name}\n")
            
            analyses = db.query(BusinessAnalysis).filter(BusinessAnalysis.user_id == u.id).all()
            commissions = db.query(Commission).filter(Commission.user_id == u.id).all()
            
            f.write(f"  Analyses count: {len(analyses)}\n")
            for a in analyses:
                f.write(f"   - Analysis: id={a.id}, goal={a.business_goal[:50]}\n")
            
            f.write(f"  Commissions count: {len(commissions)}\n")
            for c in commissions:
                f.write(f"   - Commission: id={c.id}, amount={c.amount}, status={c.status}\n")
            f.write("-" * 20 + "\n")

if __name__ == "__main__":
    debug_data()
