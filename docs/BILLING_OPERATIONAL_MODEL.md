# Billing Operational Model & Testing Guide

This document defines the professional operational standards for the Lavoo billing system, its integration with Stripe, and the procedures for both testing and live production management.

## 1. The Dual-Track Execution Model

To maintain system stability and provide the best user experience, we employ two distinct tracks for billing execution.

### Track A: Event-Driven (Immediate)
*   **Target**: New signups or existing users who proactively "Upgrade" or "Save Card" while the app is in `launch` mode.
*   **Execution**: Synchronous backend logic triggered by the API call.
*   **User Experience**: Instant feedback. The user clicks "Checkout", Stripe processes the payment/card save, and the app immediately marks them as `active` with a success message.
*   **Testing Method**: Log in to the dashboard, go to the Upgrade page, and save a card while `APP_MODE=launch` is set in `.env`.

### Track B: Scheduled-Batch (Cron)
*   **Target**: Legacy Beta users who saved their cards *before* the launch and need to be transitioned to a paid state.
*   **Execution**: Asynchronous background script (`cron/process_beta_billing.py`) run via a system scheduler.
*   **User Experience**: Passive activation. The system bills the user "behind the scenes". The user simply wakes up to an "Active" subscription and a confirmation email.
*   **Testing Method**: Set `APP_MODE=launch`, ensure you have users in the DB with `is_beta_user=True` and a `stripe_payment_method_id` but `subscription_status != 'active'`, then run `python cron/process_beta_billing.py`.

---

## 2. Stripe Lifecycle & Operational Flow

Lavoo uses Stripe **Subscriptions**, which shift the burden of recurring logic from our servers to Stripe's world-class infrastructure.

### The Lifecycle
1.  **Initial Activation (Lavoo Triggered)**: Either via Track A or Track B, Lavoo makes the first request to Stripe to `create_subscription`. This performs the initial charge.
2.  **The "Hand-off"**: Once Step 1 is successful, Stripe takes over the "Master Schedule" for that user.
3.  **Automatic Renewal (Stripe Triggered)**: Every 30 days (Monthly) or 365 days (Yearly), Stripe's servers will automatically attempt to charge the saved card. We do NOT need to run our cron job for these users ever again.
4.  **Webhooks (Synchronization)**: Stripe sends a "Webhook" (a digital ping) to our server whenever a recurring payment succeeds or fails. Our system listens to these pings to keep our database in sync with Stripe's records.

---

## 3. Professional Testing Procedure

To verify the system is ready for live production, follow these two validation paths:

### Path 1: Validating the "Launch Day" Sweep
1.  **Setup**: Use a test account. Set its status to `is_beta_user=True` and save a test card (Stripe `4242` card) while in `beta` mode.
2.  **Action**: Switch `.env` to `APP_MODE=launch`.
3.  **Execute**: Run `python cron/process_beta_billing.py`.
4.  **Verification**: Check that the user status in the database is now `active` and that a `Subscriptions` record was created.

### Path 2: Validating the "Post-Launch" Flow
1.  **Setup**: Use a new test account or one with no card. Ensure `APP_MODE=launch`.
2.  **Action**: Go to the Upgrade page and go through the "Checkout" flow.
3.  **Verification**: Verify that the charge happens **instantly** without needing to run any scripts, and the "Congratulations" success screen appears.

---

## 4. Live Production Management
In a live environment (Railway, DigitalOcean, etc.):
*   **APP_MODE**: Managed via the platform's Environment Variable dashboard.
*   **Cron Job**: The `process_beta_billing.py` should be scheduled to run once every 24 hours. (Note: It is idempotent, meaning it won't charge the same user twice because it checks for `subscription_status != 'active'`).

Testing Subscription Renewal: Stripe handles rebilling automatically. To test it:
Use Stripe's Test Clocks in the Stripe Dashboard to simulate time advancing. You can attach a test customer to a clock, advance the clock to the end of their billing cycle, and immediately observe if Stripe fires the invoice.payment_succeeded webhooks to your app.


Regarding Railway Cron Jobs: Railway handles background scheduling entirely via its UI, not via Linux crontab.

In your Railway project dashboard, click New > Service.
Select Cron Job.
Choose your repository.
Set the Cron Schedule (e.g., 0 0 * * * for daily at midnight).
In the service settings, set the Start Command to: python cron/process_beta_billing.py (or load your existing Dockerfile.cron-subscriptions image into this Railway Cron service).