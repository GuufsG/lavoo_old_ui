-- Sync referral_count for all users from actual Referral table records
-- This ensures user.referral_count matches the actual count in the referrals table

-- Step 1: Check current mismatch (optional - for verification)
SELECT u.id, u.email, u.referral_count as stored_count,
       (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.id) as actual_count
FROM users u
WHERE u.referral_count != (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.id);

-- Step 2: Sync all users' referral_count
UPDATE users u
SET referral_count = (
    SELECT COUNT(*) 
    FROM referrals 
    WHERE referrer_id = u.id
);

-- Step 3: Verify the fix
SELECT id, email, referral_count FROM users WHERE referral_count > 0;
