import { useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import {
  Elements,
  CardElement,
  useStripe,
  useElements
} from '@stripe/react-stripe-js';

const STRIPE_KEY = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY;
const stripePromise = STRIPE_KEY ? loadStripe(STRIPE_KEY) : null;

const CARD_ELEMENT_OPTIONS = {
  style: {
    base: {
      color: '#0A0A0A',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      fontSmoothing: 'antialiased',
      fontSize: '16px',
      '::placeholder': { color: '#9CA3AF' },
    },
    invalid: {
      color: '#EF4444',
      iconColor: '#EF4444',
    },
  },
  hidePostalCode: false,
};

interface CheckoutFormProps {
  amount: number;
  planType: 'monthly' | 'quarterly' | 'yearly';
  email: string;
  name: string;
  isBeta?: boolean;
  onSuccess: (response: any) => void;
  onError: (error: string) => void;
  onCancel: () => void;
}

// ---------------------------------------------------------------------------
// 3DS FLOW EXPLANATION
// ---------------------------------------------------------------------------
//
// 3D Secure (3DS) is a bank-level authentication step that some cards require
// before authorising a charge. The flow differs depending on the endpoint:
//
// BETA SAVE-CARD (/save-card-beta) in launch mode:
//   Backend creates a subscription with payment_behavior="error_if_incomplete"
//   (off-session, no user present assumption).
//   BUT — if the user IS present (i.e. they're on the checkout page right now),
//   we handle 3DS here on the frontend if the backend falls back to incomplete.
//   In practice, error_if_incomplete either succeeds or throws a CardError.
//   The requires_action path here is a safety net.
//
// SUBSCRIPTION FLOW (/create-subscription-with-saved-card):
//   Backend creates subscription with payment_behavior="default_incomplete"
//   (user IS present, 3DS expected). Returns requires_action + client_secret.
//   Frontend calls stripe.confirmCardPayment(client_secret) to show the 3DS modal.
//   After user completes 3DS, frontend calls /confirm-subscription to finalise.
//
// TESTING 3DS:
//   Use Stripe test card: 4000 0025 0000 3155 (always requires 3DS authentication)
//   Use Stripe test card: 4000 0027 6000 3184 (3DS required, then fails)
//   Normal success card:  4242 4242 4242 4242 (no 3DS, always succeeds)
//
// To test the 3DS modal appearing:
//   1. Set APP_MODE=launch in your .env
//   2. Use the checkout form with card 4000 0025 0000 3155
//   3. The modal should appear after clicking "Pay"
// ---------------------------------------------------------------------------

function CheckoutForm({
  amount,
  planType,
  email,
  name,
  isBeta = true,
  onSuccess,
  onError,
  onCancel
}: CheckoutFormProps) {
  const stripe = useStripe();
  const elements = useElements();
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string>('');
  const [cardComplete, setCardComplete] = useState(false);
  const [step, setStep] = useState<'form' | 'authenticating' | 'confirming'>('form');
  const [billingDetails, setBillingDetails] = useState({
    name,
    email,
    address: {
      line1: '',
      city: '',
      state: '',
      postal_code: '',
      country: 'US',
    },
  });

  const getAuthToken = (): string => {
    const token = localStorage.getItem('access_token') || localStorage.getItem('auth_token');
    if (!token) throw new Error('Authentication token not found. Please log in again.');
    return token;
  };

  /**
   * Handle 3DS authentication after backend returns requires_action.
   *
   * This is called regardless of which endpoint triggered the subscription —
   * both save-card-beta (launch mode) and create-subscription-with-saved-card
   * may return requires_action when a card needs 3DS.
   */
  const handle3DS = async (
    clientSecret: string,
    subscriptionId: string,
    paymentIntentId: string
  ): Promise<void> => {
    if (!stripe) throw new Error('Stripe not initialised');

    setStep('authenticating');

    // Show the 3DS modal — this opens the bank's authentication popup/redirect
    const { error: confirmError, paymentIntent } = await stripe.confirmCardPayment(clientSecret);

    if (confirmError) {
      // User cancelled, card declined in 3DS, or authentication failed
      throw new Error(confirmError.message || '3D Secure authentication failed');
    }

    if (paymentIntent.status !== 'succeeded') {
      throw new Error(`Payment status after 3DS: ${paymentIntent.status}. Please try again.`);
    }

    setStep('confirming');

    // Notify backend that 3DS succeeded — finalises the DB record
    const token = getAuthToken();
    const confirmResponse = await fetch('/api/stripe/confirm-subscription', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({
        subscription_id: subscriptionId,
        payment_intent_id: paymentIntent.id,
      }),
    });

    if (!confirmResponse.ok) {
      const data = await confirmResponse.json();
      throw new Error(data.detail || 'Subscription confirmation failed after 3DS');
    }

    const confirmData = await confirmResponse.json();
    onSuccess(confirmData);
  };

  const handleSubmit = async () => {
    if (!stripe || !elements) return;

    setProcessing(true);
    setError('');
    setStep('form');

    try {
      const cardElement = elements.getElement(CardElement);
      if (!cardElement) throw new Error('Card element not found');

      const token = getAuthToken();

      // Step 1: Create payment method from card details entered in the form
      const { error: pmError, paymentMethod } = await stripe.createPaymentMethod({
        type: 'card',
        card: cardElement,
        billing_details: billingDetails,
      });

      if (pmError) throw new Error(pmError.message);

      // Step 2: Choose endpoint based on whether we're in beta or launch flow
      //
      // isBeta=true  → /save-card-beta
      //   In beta mode:   saves card only, no charge, no 3DS
      //   In launch mode: saves card AND creates subscription, 3DS possible
      //
      // isBeta=false → /create-subscription-with-saved-card
      //   Always creates subscription immediately, 3DS possible
      const endpoint = isBeta
        ? '/api/stripe/save-card-beta'
        : '/api/stripe/create-subscription-with-saved-card';

      const payload = isBeta
        ? { 
            payment_method_id: paymentMethod.id,
            plan_type: planType,   // required so backend saves the correct plan during beta
          }
        : {
            payment_method_id: paymentMethod.id,
            plan_type: planType,
            billing_details: billingDetails,
          };

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Payment request failed');
      }

      const data = await response.json();

      // Step 3: Handle the response
      //
      // 'success' or 'active'  → card saved / subscription active, no 3DS needed
      // 'requires_action'      → bank requires 3DS — show authentication modal
      //
      // Note: In beta mode (APP_MODE=beta), save-card-beta always returns 'success'
      // because no charge is attempted. The requires_action path only fires in
      // launch mode when a subscription is created and the card requires 3DS.

      if (data.status === 'success' || data.status === 'active') {
        onSuccess(data);

      } else if (data.status === 'requires_action') {
        // 3DS required — validate we have everything we need
        if (!data.client_secret) {
          throw new Error(
            'Your bank requires additional verification but authentication details are missing. ' +
            'Please try again or use a different card.'
          );
        }

        if (!data.subscription_id) {
          throw new Error('Subscription ID missing from 3DS response. Please contact support.');
        }

        // Trigger the 3DS modal — this shows the bank's authentication UI
        await handle3DS(
          data.client_secret,
          data.subscription_id,
          data.payment_intent_id
        );

      } else {
        throw new Error(`Unexpected response status: ${data.status}`);
      }

    } catch (err: any) {
      const message = err?.message || 'An error occurred during payment';
      setError(message);
      onError(message);
      setStep('form');
    } finally {
      setProcessing(false);
    }
  };

  // Step label shown in the button while processing
  const getProcessingLabel = () => {
    switch (step) {
      case 'authenticating': return 'Authenticating with your bank...';
      case 'confirming':     return 'Confirming subscription...';
      default:               return 'Processing...';
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="border-b border-gray-200 pb-4">
        <h3 className="text-lg font-semibold text-gray-900">Payment Details</h3>
        <p className="text-sm text-gray-600 mt-1">
          {isBeta
            ? 'Save your card to secure automatic billing at launch'
            : 'Complete your subscription setup'}
        </p>
      </div>

      {/* 3DS in-progress notice */}
      {step === 'authenticating' && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 flex items-start gap-3">
          <div className="animate-spin w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full flex-shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-blue-900 text-sm">Bank Authentication Required</p>
            <p className="text-blue-700 text-xs mt-1">
              A verification popup has been opened by your bank. Please complete the
              authentication to continue. Do not close this page.
            </p>
          </div>
        </div>
      )}

      {step === 'confirming' && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-start gap-3">
          <div className="animate-spin w-5 h-5 border-2 border-green-500 border-t-transparent rounded-full flex-shrink-0 mt-0.5" />
          <p className="text-green-800 text-sm font-medium">
            Authentication successful. Confirming your subscription...
          </p>
        </div>
      )}

      {/* Billing Information */}
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Full Name <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={billingDetails.name}
            onChange={(e) => setBillingDetails({ ...billingDetails, name: e.target.value })}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Email <span className="text-red-500">*</span>
          </label>
          <input
            type="email"
            value={billingDetails.email}
            onChange={(e) => setBillingDetails({ ...billingDetails, email: e.target.value })}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Address Line 1 <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={billingDetails.address.line1}
            onChange={(e) => setBillingDetails({
              ...billingDetails,
              address: { ...billingDetails.address, line1: e.target.value }
            })}
            placeholder="Street address"
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
            required
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              City <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={billingDetails.address.city}
              onChange={(e) => setBillingDetails({
                ...billingDetails,
                address: { ...billingDetails.address, city: e.target.value }
              })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              State <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={billingDetails.address.state}
              onChange={(e) => setBillingDetails({
                ...billingDetails,
                address: { ...billingDetails.address, state: e.target.value }
              })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
              required
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              ZIP Code <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={billingDetails.address.postal_code}
              onChange={(e) => setBillingDetails({
                ...billingDetails,
                address: { ...billingDetails.address, postal_code: e.target.value }
              })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Country <span className="text-red-500">*</span>
            </label>
            <select
              value={billingDetails.address.country}
              onChange={(e) => setBillingDetails({
                ...billingDetails,
                address: { ...billingDetails.address, country: e.target.value }
              })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
            >
              <option value="US">United States</option>
              <option value="CA">Canada</option>
              <option value="GB">United Kingdom</option>
              <option value="NG">Nigeria</option>
              <option value="GH">Ghana</option>
              <option value="KE">Kenya</option>
              <option value="ZA">South Africa</option>
            </select>
          </div>
        </div>
      </div>

      {/* Card Information */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Card Information <span className="text-red-500">*</span>
        </label>
        <div className="border border-gray-300 rounded-lg p-3 focus-within:ring-2 focus-within:ring-orange-500 focus-within:border-orange-500">
          <CardElement
            options={CARD_ELEMENT_OPTIONS}
            onChange={(e) => {
              setCardComplete(e.complete);
              if (e.error) setError(e.error.message);
              else setError('');
            }}
          />
        </div>

        {/* Test card hints — visible in development only */}
        {import.meta.env.DEV && (
          <div className="mt-2 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
            <p className="text-xs font-semibold text-yellow-800 mb-1">🧪 Test Cards</p>
            <div className="space-y-1 text-xs text-yellow-700 font-mono">
              <div>
                <span className="font-semibold">No 3DS:</span> 4242 4242 4242 4242
              </div>
              <div>
                <span className="font-semibold">3DS required (succeeds):</span> 4000 0025 0000 3155
              </div>
              <div>
                <span className="font-semibold">3DS required (fails):</span> 4000 0027 6000 3184
              </div>
              <div className="text-yellow-600 mt-1">
                Any future expiry · Any 3-digit CVC · Any ZIP
              </div>
              <div className="text-yellow-600 mt-1 font-sans font-medium">
                ⚠️ 3DS modal only appears in <strong>launch mode</strong> (APP_MODE=launch)
              </div>
            </div>
          </div>
        )}

        <p className="text-xs text-gray-500 mt-2 flex items-center gap-1">
          <i className="ri-lock-line"></i>
          Your card details are encrypted and secure. We never store your full card number.
        </p>
      </div>

      {/* Security info */}
      <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
        <div className="flex items-start gap-3">
          <i className="ri-shield-check-line text-green-600 text-xl flex-shrink-0 mt-0.5"></i>
          <div>
            <h4 className="font-medium text-gray-900 text-sm mb-1">Secure Payment Processing</h4>
            <ul className="text-xs text-gray-600 space-y-1">
              <li>• PCI DSS Level 1 compliant</li>
              <li>• 256-bit SSL encryption</li>
              <li>• {planType === 'monthly' ? 'Billed monthly' : planType === 'quarterly' ? 'Billed quarterly' : 'Billed annually'} until you cancel</li>
              {isBeta && <li>• Card saved now, billing begins at launch</li>}
            </ul>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-red-600 text-sm flex items-start gap-2">
            <i className="ri-error-warning-line flex-shrink-0 mt-0.5"></i>
            {error}
          </p>
        </div>
      )}

      {/* Order Summary */}
      {!isBeta && (
        <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700">Plan</span>
            <span className="text-sm text-gray-900 capitalize">{planType}</span>
          </div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700">Billing Frequency</span>
            <span className="text-sm text-gray-900">
              {planType === 'monthly' ? 'Every month' : planType === 'quarterly' ? 'Every 3 months' : 'Every year'}
            </span>
          </div>
          <div className="border-t border-orange-300 my-2"></div>
          <div className="flex items-center justify-between">
            <span className="text-base font-semibold text-gray-900">Total Due Today</span>
            <span className="text-xl font-bold text-orange-600">${amount.toFixed(2)}</span>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button
          onClick={onCancel}
          className="flex-1 px-6 py-3 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 font-medium transition-colors"
          disabled={processing}
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!stripe || processing || !cardComplete}
          className="flex-1 px-6 py-3 bg-orange-600 text-white rounded-lg hover:bg-orange-700 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {processing ? (
            <>
              <div className="animate-spin w-5 h-5 border-2 border-white border-t-transparent rounded-full" />
              {getProcessingLabel()}
            </>
          ) : (
            <>
              <i className="ri-lock-line"></i>
              {isBeta ? 'Save Card' : `Pay $${amount.toFixed(2)}`}
            </>
          )}
        </button>
      </div>

      {/* Trust indicators */}
      <div className="text-center">
        <div className="flex items-center justify-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <i className="ri-shield-check-line text-green-600"></i>
            Secure
          </span>
          <span className="flex items-center gap-1">
            <i className="ri-time-line text-blue-600"></i>
            Cancel Anytime
          </span>
          <span className="flex items-center gap-1">
            <i className="ri-customer-service-line text-purple-600"></i>
            24/7 Support
          </span>
        </div>
      </div>
    </div>
  );
}

export default function StripeCheckoutWithSavedCard(props: CheckoutFormProps) {
  if (!stripePromise) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-600">
        Stripe configuration error. Please contact support.
      </div>
    );
  }

  return (
    <Elements stripe={stripePromise}>
      <CheckoutForm {...props} />
    </Elements>
  );
}