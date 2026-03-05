
from db.pg_connections import get_db
from db.pg_models import User
import sys

def set_beta_users():
    """
    Update all existing users to be beta users.
    """
    db = next(get_db())
    try:
        updated = db.query(User).update({User.is_beta_user: True})
        db.commit()
        print(f"✅ Successfully updated {updated} users to beta status.")
    except Exception as e:
        db.rollback()
        print(f"❌ Error updating users: {str(e)}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    set_beta_users()
