import os
import sys
from datetime import datetime, timedelta

# Add parent dir to path so we can import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from db.pg_models import User

def backdate_user(email: str, days: int):
    """
    Backdate a user's beta_joined_at and grace_period_ends_at by `days`.
    This brings them closer to (or past) their expiration date for testing purposes.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"Error: User with email '{email}' not found.")
            return

        print(f"User found: {user.name} ({user.email})")

        if user.grace_period_ends_at:
            old_grace = user.grace_period_ends_at
            user.grace_period_ends_at = old_grace - timedelta(days=days)
            print(f"Changed grace_period_ends_at from {old_grace} to {user.grace_period_ends_at}")
            
        if user.beta_joined_at:
            old_join = user.beta_joined_at
            user.beta_joined_at = old_join - timedelta(days=days)
            print(f"Changed beta_joined_at from {old_join} to {user.beta_joined_at}")

        db.commit()
        print(f"Successfully backdated user '{email}' by {days} days.")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python backdate_user.py <email> <days_to_subtract>")
        print("Example: python backdate_user.py test@example.com 6")
        sys.exit(1)

    email = sys.argv[1]
    
    try:
        days = int(sys.argv[2])
    except ValueError:
        print("Error: <days_to_subtract> must be an integer.")
        sys.exit(1)

    backdate_user(email, days)
