
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not found")
    exit(1)

# Clean up the URL if it has quotes
db_url = db_url.strip("'")

sql_commands = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_beta_user BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS beta_joined_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_payment_method_id VARCHAR(255);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_last4 VARCHAR(4);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_brand VARCHAR(50);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_exp_month INTEGER;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_exp_year INTEGER;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS card_saved_at TIMESTAMP;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;",
    """
    CREATE TABLE IF NOT EXISTS notification_history (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        notification_type VARCHAR(50) NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_notification_history_user_type_sent ON notification_history (user_id, notification_type, sent_at);"
]

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    for sql in sql_commands:
        print(f"Executing: {sql[:50]}...")
        cur.execute(sql)
    conn.commit()
    print("✅ Migrations completed successfully")
except Exception as e:
    print(f"❌ Migration failed: {e}")
finally:
    if 'conn' in locals():
        conn.close()
