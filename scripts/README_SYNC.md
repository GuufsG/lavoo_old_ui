# How to Run the Referral Count Sync

I've created the SQL file for you at: `scripts/sync_referral_counts.sql`

## Option 1: Using psql (Recommended)

Run this command in your terminal:

```bash
psql "postgresql://neondb_owner:npg_syBmqr5twj4K@ep-crimson-bush-abi04jqe-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require" -f scripts/sync_referral_counts.sql
```

## Option 2: Using the bash script

```bash
bash scripts/run_sync.sh
```

## Option 3: Run SQL directly in your database client

Open your database client (pgAdmin, DBeaver, etc.) and run the contents of `scripts/sync_referral_counts.sql`

## What the sync does:

1. Checks which users have mismatched referral_count
2. Updates all users' referral_count to match actual records in the referrals table
3. Shows you the updated counts

## After running:

1. Refresh your dashboard
2. The "Total Referrals" should now show the correct count
3. Check the browser console for the debug logs to verify the API is returning correct data

---

**Note**: The sync query is safe to run multiple times. It will always ensure the counts are accurate.
