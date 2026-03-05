# Database Sync Queries

## Problem Identified

The `user.referral_count` field in the database is out of sync with the actual count in the `referrals` table.

- **Dashboard** displays `user.referral_count` from the users table (currently showing 0)
- **Earnings page** counts actual records from the `referrals` table (shows 1 - correct!)

## SQL Queries to Fix

### 1. Check Current Mismatch

```sql
SELECT u.id, u.email, u.referral_count as stored_count,
       (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.id) as actual_count
FROM users u
WHERE u.referral_count != (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.id);
```

### 2. Sync All Users' Referral Count

```sql
UPDATE users u
SET referral_count = (
    SELECT COUNT(*) 
    FROM referrals 
    WHERE referrer_id = u.id
);
```

### 3. Verify the Fix

```sql
SELECT id, email, referral_count FROM users WHERE referral_count > 0;
```

## Additional Diagnostic Queries

### Check Commissions

```sql
SELECT user_id, SUM(amount) as total, status 
FROM commissions 
GROUP BY user_id, status;
```

### Check Analyses

```sql
SELECT user_id, COUNT(*) as count 
FROM business_analyses 
GROUP BY user_id;
```

## Next Steps

1. Run the sync query (#2 above)
2. Refresh the dashboard
3. Verify that referral count displays correctly
4. Check if commission and analyses data also needs syncing
