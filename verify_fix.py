import os
import sys
from sqlalchemy.orm import Session
from sqlalchemy import func

# Add project root to path
sys.path.append(os.getcwd())

from db.pg_connections import SessionLocal
from db.pg_models import User, BusinessAnalysis, Commission

def verify_fix():
    db = SessionLocal()
    try:
        # Simulate User Stats Logic
        user_id = 18 # Ebube
        print(f"\n=== Simulating User {user_id} Stats ===")
        
        # 1. Core Stat (Should always succeed now)
        try:
            total_analyses = db.query(func.count(BusinessAnalysis.id)).filter(
                BusinessAnalysis.user_id == user_id
            ).scalar() or 0
            print(f"SUCCESS: Total Analyses = {total_analyses}")
        except Exception as e:
            print(f"FAIL: Total Analyses raised {e}")

        # 2. Secondary Stat (Could fail, but shouldn't crash script)
        try:
             # Simulate broken duration
            total_seconds = 0.0
            analyses_with_duration = db.query(BusinessAnalysis.duration).filter(
                BusinessAnalysis.user_id == user_id
            ).all()
            
            for row in analyses_with_duration:
                # Force failure simulation if needed, but existing data might be fine
                if row.duration:
                     try:
                        cleaned = row.duration.lower().replace('s', '').strip()
                        total_seconds += float(cleaned)
                     except ValueError:
                        print(f"Caught expected ValueError for duration: {row.duration}")
            print(f"SUCCESS: Total Seconds = {total_seconds}")
            
        except Exception as e:
            print(f"FAIL: Duration calc raised {e}")

    finally:
        db.close()

if __name__ == "__main__":
    verify_fix()
