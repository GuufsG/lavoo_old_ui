import { useState, useEffect } from 'react';
import { toast } from 'react-toastify';
import StripeCheckoutWithSavedCard from './checkoutForm';
import { useBetaStatus, useCurrentUser } from '../../../api/user';

export default function UpgradePage() {
  const [selectedPlan, setSelectedPlan] = useState<'monthly' | 'quarterly' | 'yearly'>('monthly');
  const [isProcessing, setIsProcessing] = useState(false);
  const [paymentSuccess, setPaymentSuccess] = useState(false);
  const [paymentError, setPaymentError] = useState('');
  const [userData, setUserData] = useState<any>(null);
  const { data: betaStatus, refetch: refetchBetaStatus } = useBetaStatus();
  const { refetch: refetchUser } = useCurrentUser();

  const [showPayoutSetup, setShowPayoutSetup] = useState(false);
  const [payoutMethod, setPayoutMethod] = useState<'stripe' | 'flutterwave' | null>(null);
  const [payoutAccount, setPayoutAccount] = useState<any>(null);
  const [loadingPayoutAccount, setLoadingPayoutAccount] = useState(false);
  const [showCheckout, setShowCheckout] = useState(false);
  const [activeTab, setActiveTab] = useState<'plans' | 'history' | 'manage_card'>('plans');
  const [subscriptionHistory, setSubscriptionHistory] = useState<any[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

  // Flutterwave bank details
  const [bankDetails, setBankDetails] = useState({
    bank_name: '',
    account_number: '',
    account_name: '',
    bank_code: '',
  });
  const [verifyingAccount, setVerifyingAccount] = useState(false);
  const [savingBankDetails, setSavingBankDetails] = useState(false);
  const [accountVerifyError, setAccountVerifyError] = useState('');

  // Dynamic Pricing State
  const [pricing, setPricing] = useState({
    monthly: 29.95,
    quarterly: 79.95,
    yearly: 290.00
  });

  const plans = {
    monthly: { price: pricing.monthly, name: 'Monthly Plan' },
    quarterly: { price: pricing.quarterly, name: 'Quarterly Plan' },
    yearly: { price: pricing.yearly, name: 'Yearly Plan' }
  };

  useEffect(() => {
    fetchUserData();
    fetchPayoutAccount();
    fetchSettings();
    if (activeTab === 'history') {
      fetchSubscriptionHistory();
    }
  }, [activeTab]);

  const fetchSettings = async () => {
    try {
      const token = localStorage.getItem('access_token') || localStorage.getItem('auth_token');
      const response = await fetch('/api/control/settings', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });
      if (response.ok) {
        const data = await response.json();
        // Update pricing if data exists
        if (data.monthly_price || data.quarterly_price || data.yearly_price) {
          setPricing({
            monthly: data.monthly_price || 29.95,
            quarterly: data.quarterly_price || 79.95,
            yearly: data.yearly_price || 290.00
          });
        }
      }
    } catch (error) {
      console.error("Error fetching settings for pricing:", error);
    }
  };

  const fetchUserData = async () => {
    try {
      const token = localStorage.getItem('access_token') || localStorage.getItem('auth_token');
      const response = await fetch('/api/user/me', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setUserData(data);
      }
    } catch (error) {
      console.error('Error fetching user data:', error);
    }
  };

  const fetchSubscriptionHistory = async () => {
    try {
      setIsLoadingHistory(true);
      const token = document.cookie
        .split('; ')
        .find((row) => row.startsWith('access_token='))
        ?.split('=')[1];

      const response = await fetch('/api/stripe/history', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setSubscriptionHistory(data);
      }
    } catch (error) {
      console.error('Error fetching subscription history:', error);
      toast.error('Failed to load payment history');
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const fetchPayoutAccount = async () => {
    try {
      setLoadingPayoutAccount(true);
      const getCookie = (name: string) => {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
      };
      const token = getCookie('access_token') || localStorage.getItem('access_token') || localStorage.getItem('auth_token');

      const response = await fetch('/api/commissions/payout-account', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        if (data.status === 'success') {
          setPayoutAccount(data.data);
        }
      }
    } catch (error) {
      console.error('Error fetching payout account:', error);
    } finally {
      setLoadingPayoutAccount(false);
    }
  };

  const connectStripe = async () => {
    try {
      setIsProcessing(true);
      const token = document.cookie
        .split('; ')
        .find((row) => row.startsWith('access_token='))
        ?.split('=')[1];

      const response = await fetch('/api/stripe/connect/onboard', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        throw new Error('Failed to initiate Stripe Connect');
      }

      const data = await response.json();
      window.location.href = data.onboarding_url;
    } catch (error: any) {
      setPaymentError(error.message || 'Failed to connect Stripe account');
      setIsProcessing(false);
    }
  };

  const verifyBankAccount = async () => {
    if (!bankDetails.account_number || !bankDetails.bank_code) {
      setAccountVerifyError('Please enter account number and bank code');
      return;
    }

    try {
      setVerifyingAccount(true);
      setAccountVerifyError('');

      const token = document.cookie
        .split('; ')
        .find((row) => row.startsWith('access_token='))
        ?.split('=')[1];

      console.log('Verifying account:', {
        account_number: bankDetails.account_number,
        bank_code: bankDetails.bank_code.toString()
      });

      const response = await fetch('/api/payments/flutterwave/verify-account', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          account_number: bankDetails.account_number,
          bank_code: bankDetails.bank_code,
        }),
      });

      const data = await response.json();
      console.log('Verification response:', data);

      if (response.ok && data.status === 'success') {
        setBankDetails(prev => ({
          ...prev,
          account_name: data.account_name
        }));
        setAccountVerifyError('');
        toast.success(`Account verified: ${data.account_name}`);
      } else {
        const errorMsg = data.detail || 'Account verification failed';
        setAccountVerifyError(errorMsg);
        console.error('Verification failed:', errorMsg);
      }
    } catch (error: any) {
      console.error('Error verifying account:', error);
      const errorMsg = error.message || 'Failed to verify account. Please try again.';
      setAccountVerifyError(errorMsg);
    } finally {
      setVerifyingAccount(false);
    }
  };

  const saveBankDetails = async () => {
    if (!bankDetails.bank_name || !bankDetails.account_number || !bankDetails.account_name) {
      toast.error('Please fill in all required fields and verify your account');
      return;
    }

    try {
      setSavingBankDetails(true);
      const token = localStorage.getItem('access_token') ||
        localStorage.getItem('auth_token') ||
        document.cookie
          .split('; ')
          .find((row) => row.startsWith('access_token='))
          ?.split('=')[1];

      const response = await fetch('/api/commissions/payout-account', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          payment_method: 'flutterwave',
          ...bankDetails
        }),
      });

      if (response.ok) {
        toast.success('Bank details saved successfully!');
        setShowPayoutSetup(false);
        setPayoutMethod(null);
        fetchPayoutAccount();
        // Reset form
        setBankDetails({
          bank_name: '',
          account_number: '',
          account_name: '',
          bank_code: '',
        });
        setAccountVerifyError('');
      } else {
        const errorData = await response.json();
        toast.error(`Failed to save: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error saving bank details:', error);
      toast.error('Failed to save bank details');
    } finally {
      setSavingBankDetails(false);
    }
  };

  const handlePaymentSuccess = (response: any) => {
    console.log('Payment successful:', response);
    toast.success(response.message || 'Payment successful!');
    setPaymentSuccess(true);
    setIsProcessing(false);
    setPaymentError('');

    // Refetch user data to update the "Manage Card" tab
    refetchUser();
    refetchBetaStatus();

    setTimeout(() => {
      window.location.href = '/dashboard';
    }, 3000);
  };

  const handlePaymentError = (error: string) => {
    console.error('Payment error:', error);
    setPaymentError(error);
    setIsProcessing(false);
  };

  if (!userData) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-orange-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 to-white py-8 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-3xl sm:text-4xl font-bold text-gray-900 mb-2">
            Upgrade Your Plan
          </h1>
          <p className="text-gray-600">
            Choose the plan that works best for you or view your payment history
          </p>
        </div>


        {/* Tabs */}
        <div className="flex justify-center mb-8">
          <div className="flex border-b border-gray-200 w-full max-w-lg">
            <button
              onClick={() => setActiveTab('plans')}
              className={`flex-1 py-4 text-center font-medium transition-all relative ${activeTab === 'plans' ? 'text-orange-600' : 'text-gray-500 hover:text-gray-700'}`}
            >
              Plans & Pricing
              {activeTab === 'plans' && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-orange-500"></div>
              )}
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`flex-1 py-4 text-center font-medium transition-all relative ${activeTab === 'history' ? 'text-orange-600' : 'text-gray-500 hover:text-gray-700'}`}
            >
              Payment History
              {activeTab === 'history' && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-orange-500"></div>
              )}
            </button>
            {userData.stripe_payment_method_id && (
              <button
                onClick={() => setActiveTab('manage_card')}
                className={`flex-1 py-4 text-center font-medium transition-all relative ${activeTab === 'manage_card' ? 'text-orange-600' : 'text-gray-500 hover:text-gray-700'}`}
              >
                Manage Card
                {activeTab === 'manage_card' && (
                  <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-orange-500"></div>
                )}
              </button>
            )}
          </div>
        </div>

        {activeTab === 'plans' && (
          <>

            {/* Payout Setup Banner */}
            {!loadingPayoutAccount && !payoutAccount && (
              <div className="bg-gradient-to-r from-purple-50 to-blue-50 border border-purple-200 rounded-xl p-6 mb-8">
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0">
                      <i className="ri-bank-line text-purple-600 text-xl"></i>
                    </div>
                    <div>
                      <h3 className="font-semibold text-gray-900 mb-1">Set Up Payout Account</h3>
                      <p className="text-sm text-gray-600">
                        Earn 50% commission on referrals! Set up your payout method to receive earnings.
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => setShowPayoutSetup(true)}
                    className="bg-purple-600 text-white px-6 py-2 rounded-lg hover:bg-purple-700 transition-colors font-medium whitespace-nowrap"
                  >
                    Set Up Now
                  </button>
                </div>
              </div>
            )}

            {/* Payout Account Status */}
            {payoutAccount && (
              <div className="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-xl p-6 mb-8">
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center flex-shrink-0">
                    <i className="ri-check-line text-green-600 text-xl"></i>
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold text-gray-900 mb-1">Payout Account Active</h3>
                    <p className="text-sm text-gray-600 mb-2">
                      {payoutAccount.payment_method === 'stripe' && 'Connected with Stripe Connect'}
                      {payoutAccount.payment_method === 'flutterwave' && `Bank: ${payoutAccount.bank_name}`}
                    </p>
                    <button
                      onClick={() => setShowPayoutSetup(true)}
                      className="text-sm text-purple-600 hover:text-purple-700 font-medium"
                    >
                      Update payout method →
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Payout Setup Modal */}
            {showPayoutSetup && (
              <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
                <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
                  <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
                    <h2 className="text-xl font-bold text-gray-900">Set Up Payout Account</h2>
                    <button
                      onClick={() => {
                        setShowPayoutSetup(false);
                        setPayoutMethod(null);
                        setAccountVerifyError('');
                      }}
                      className="text-gray-400 hover:text-gray-600"
                    >
                      <i className="ri-close-line text-2xl"></i>
                    </button>
                  </div>

                  <div className="p-6">
                    {!payoutMethod ? (
                      <div className="space-y-4">
                        <p className="text-gray-600 mb-6">
                          Choose how you'd like to receive your referral commissions (50% of each subscription):
                        </p>

                        {/* Stripe Option */}
                        <button
                          onClick={() => setPayoutMethod('stripe')}
                          className="w-full border-2 border-gray-200 rounded-xl p-6 hover:border-purple-500 hover:bg-purple-50 transition-all text-left group"
                        >
                          <div className="flex items-start gap-4">
                            <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-purple-200 transition-colors">
                              <i className="ri-bank-card-line text-purple-600 text-2xl"></i>
                            </div>
                            <div className="flex-1">
                              <h3 className="font-semibold text-gray-900 mb-1">Stripe Connect</h3>
                              <p className="text-sm text-gray-600 mb-2">
                                Connect your bank account securely through Stripe. Fast, automated payouts.
                              </p>
                              <div className="flex flex-wrap gap-2">
                                <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded">Automated</span>
                                <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded">Secure</span>
                                <span className="text-xs bg-purple-100 text-purple-700 px-2 py-1 rounded">International</span>
                              </div>
                            </div>
                            <i className="ri-arrow-right-line text-gray-400 text-xl group-hover:text-purple-600 transition-colors"></i>
                          </div>
                        </button>

                        {/* Flutterwave Option */}
                        <button
                          onClick={() => setPayoutMethod('flutterwave')}
                          className="w-full border-2 border-gray-200 rounded-xl p-6 hover:border-orange-500 hover:bg-orange-50 transition-all text-left group"
                        >
                          <div className="flex items-start gap-4">
                            <div className="w-12 h-12 bg-orange-100 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-orange-200 transition-colors">
                              <i className="ri-bank-line text-orange-600 text-2xl"></i>
                            </div>
                            <div className="flex-1">
                              <h3 className="font-semibold text-gray-900 mb-1">Bank Transfer (Flutterwave)</h3>
                              <p className="text-sm text-gray-600 mb-2">
                                Provide your bank details for direct transfers. Perfect for African banks.
                              </p>
                              <div className="flex flex-wrap gap-2">
                                <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded">Direct Transfer</span>
                                <span className="text-xs bg-orange-100 text-orange-700 px-2 py-1 rounded">Africa-Friendly</span>
                              </div>
                            </div>
                            <i className="ri-arrow-right-line text-gray-400 text-xl group-hover:text-orange-600 transition-colors"></i>
                          </div>
                        </button>
                      </div>
                    ) : payoutMethod === 'stripe' ? (
                      <div className="space-y-6">
                        <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
                          <div className="flex gap-3">
                            <i className="ri-information-line text-purple-600 text-xl"></i>
                            <div>
                              <h4 className="font-medium text-gray-900 mb-1">Stripe Connect Onboarding</h4>
                              <p className="text-sm text-gray-600">
                                You'll be redirected to Stripe to securely connect your bank account.
                                This usually takes 2-3 minutes.
                              </p>
                            </div>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <div className="flex items-start gap-2">
                            <i className="ri-check-line text-green-600 mt-1"></i>
                            <span className="text-sm text-gray-700">Secure bank verification by Stripe</span>
                          </div>
                          <div className="flex items-start gap-2">
                            <i className="ri-check-line text-green-600 mt-1"></i>
                            <span className="text-sm text-gray-700">Automated payout processing</span>
                          </div>
                          <div className="flex items-start gap-2">
                            <i className="ri-check-line text-green-600 mt-1"></i>
                            <span className="text-sm text-gray-700">Works with most international banks</span>
                          </div>
                        </div>

                        <div className="flex gap-3">
                          <button
                            onClick={() => setPayoutMethod(null)}
                            className="flex-1 px-4 py-3 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 font-medium"
                          >
                            Back
                          </button>
                          <button
                            onClick={connectStripe}
                            disabled={isProcessing}
                            className="flex-1 px-4 py-3 bg-purple-600 text-white rounded-lg hover:bg-purple-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                          >
                            {isProcessing ? (
                              <>
                                <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
                                Connecting...
                              </>
                            ) : (
                              <>
                                Connect with Stripe
                                <i className="ri-external-link-line"></i>
                              </>
                            )}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-6">
                        <div className="bg-orange-50 border border-orange-200 rounded-lg p-4">
                          <div className="flex gap-3">
                            <i className="ri-information-line text-orange-600 text-xl"></i>
                            <div>
                              <h4 className="font-medium text-gray-900 mb-1">Bank Account Details</h4>
                              <p className="text-sm text-gray-600">
                                Enter your bank details to receive commission payouts directly to your account.
                              </p>
                            </div>
                          </div>
                        </div>

                        {/* Show error message */}
                        {accountVerifyError && (
                          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                            <div className="flex gap-2">
                              <i className="ri-error-warning-line text-red-600 text-xl flex-shrink-0"></i>
                              <p className="text-sm text-red-600">{accountVerifyError}</p>
                            </div>
                          </div>
                        )}

                        <div className="space-y-4">
                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              Bank Name <span className="text-red-500">*</span>
                            </label>
                            <input
                              type="text"
                              value={bankDetails.bank_name}
                              onChange={(e) => setBankDetails({ ...bankDetails, bank_name: e.target.value })}
                              placeholder="e.g., Access Bank"
                              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
                            />
                          </div>

                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              Bank Code <span className="text-red-500">*</span>
                            </label>
                            <input
                              type="text"
                              value={bankDetails.bank_code}
                              onChange={(e) => setBankDetails({ ...bankDetails, bank_code: e.target.value })}
                              placeholder="e.g., 044 (for Nigerian banks)"
                              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
                            />
                            <p className="text-xs text-gray-500 mt-1">
                              Required for bank verification. Find your bank code online.
                            </p>
                          </div>

                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              Account Number <span className="text-red-500">*</span>
                            </label>
                            <div className="flex gap-2">
                              <input
                                type="text"
                                value={bankDetails.account_number}
                                onChange={(e) => {
                                  setBankDetails({ ...bankDetails, account_number: e.target.value });
                                  setAccountVerifyError('');
                                }}
                                placeholder="0123456789"
                                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
                              />
                              <button
                                onClick={verifyBankAccount}
                                disabled={verifyingAccount || !bankDetails.account_number || !bankDetails.bank_code}
                                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                              >
                                {verifyingAccount ? 'Verifying...' : 'Verify'}
                              </button>
                            </div>
                          </div>

                          <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                              Account Name <span className="text-red-500">*</span>
                            </label>
                            <input
                              type="text"
                              value={bankDetails.account_name}
                              onChange={(e) => setBankDetails({ ...bankDetails, account_name: e.target.value })}
                              placeholder="Will be auto-filled after verification"
                              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500 bg-gray-50"
                              readOnly
                            />
                          </div>
                        </div>

                        <div className="flex gap-3">
                          <button
                            onClick={() => {
                              setPayoutMethod(null);
                              setAccountVerifyError('');
                            }}
                            className="flex-1 px-4 py-3 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 font-medium"
                          >
                            Back
                          </button>
                          <button
                            onClick={saveBankDetails}
                            disabled={savingBankDetails || !bankDetails.bank_name || !bankDetails.account_number || !bankDetails.account_name}
                            className="flex-1 px-4 py-3 bg-orange-600 text-white rounded-lg hover:bg-orange-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                          >
                            {savingBankDetails ? (
                              <>
                                <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
                                Saving...
                              </>
                            ) : (
                              'Save Bank Details'
                            )}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Plan Selection */}
            <div className="flex p-1 bg-gray-100 rounded-xl mb-8 w-fit mx-auto">
              <button
                onClick={() => {
                  setSelectedPlan('monthly');
                  setShowCheckout(false);
                }}
                className={`px-6 py-2 rounded-lg text-sm font-medium transition-all ${selectedPlan === 'monthly'
                  ? 'bg-white text-orange-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
                  }`}
              >
                Monthly
              </button>
              <button
                onClick={() => {
                  setSelectedPlan('quarterly');
                  setShowCheckout(false);
                }}
                className={`px-6 py-2 rounded-lg text-sm font-medium transition-all ${selectedPlan === 'quarterly'
                  ? 'bg-white text-orange-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
                  }`}
              >
                Quarterly
              </button>
              <button
                onClick={() => {
                  setSelectedPlan('yearly');
                  setShowCheckout(false);
                }}
                className={`px-6 py-2 rounded-lg text-sm font-medium transition-all ${selectedPlan === 'yearly'
                  ? 'bg-white text-orange-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
                  }`}
              >
                Yearly
                {pricing.monthly && pricing.yearly && (
                  <span className="ml-2 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                    Save {Math.round(((pricing.monthly * 12 - pricing.yearly) / (pricing.monthly * 12)) * 100)}%
                  </span>
                )}
              </button>
            </div>

            {/* Active Subscription Banner - TOP LEVEL */}
            {userData.subscription_status === 'active' && userData.stripe_payment_method_id && !showCheckout && (
              <div className="max-w-4xl mx-auto mb-10">
                <div className="bg-gradient-to-r from-orange-500 to-orange-600 rounded-2xl shadow-xl overflow-hidden text-white">
                  <div className="px-8 py-10 flex flex-col md:flex-row items-center justify-between gap-8">
                    <div className="flex items-center gap-6">
                      <div className="w-20 h-20 bg-white/20 backdrop-blur-sm rounded-2xl flex items-center justify-center border border-white/30">
                        <i className="ri-medal-line text-4xl"></i>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="bg-white/20 text-white text-xs font-bold px-2 py-0.5 rounded uppercase tracking-wider">Active Plan</span>
                          <span className="text-orange-100/80 text-sm">•</span>
                          <span className="text-orange-50 font-medium">Monthly Billing</span>
                        </div>
                        <h2 className="text-3xl font-extrabold capitalize">{userData.subscription_plan} Analysis Engine</h2>
                        <div className="flex items-center gap-4 mt-3 text-orange-50">
                          <div className="flex items-center gap-1.5 bg-black/10 px-3 py-1 rounded-full text-sm">
                            <i className="ri-calendar-event-line text-orange-200"></i>
                            Next Bill: {userData.subscription_expires_at
                              ? new Date(userData.subscription_expires_at).toLocaleDateString('en-US', { day: 'numeric', month: 'long', year: 'numeric' })
                              : 'Not Available'}
                          </div>
                          <div className="flex items-center gap-1.5 bg-black/10 px-3 py-1 rounded-full text-sm">
                            <i className="ri-shield-check-line text-orange-200"></i>
                            Premium Verified
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3">
                      <button
                        onClick={() => {
                          const historyTab = document.querySelector('[data-tab="history"]') as HTMLElement;
                          if (historyTab) historyTab.click();
                        }}
                        className="px-6 py-3 bg-white/10 hover:bg-white/20 backdrop-blur-sm border border-white/30 rounded-xl font-bold transition-all"
                      >
                        Billing History
                      </button>
                      {userData.subscription_plan === 'monthly' && (
                        <button
                          onClick={() => {
                            setSelectedPlan('yearly');
                            setShowCheckout(true);
                          }}
                          className="px-6 py-3 bg-white text-orange-600 hover:bg-orange-50 rounded-xl font-bold shadow-lg transition-all"
                        >
                          Upgrade to Yearly
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* Status Indicator for non-yearly users */}
                {userData.subscription_plan !== 'yearly' && (
                  <div className="mt-6 bg-white border border-gray-100 rounded-xl p-4 flex items-center justify-between shadow-sm">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 bg-orange-50 rounded-lg flex items-center justify-center">
                        <i className="ri-arrow-up-circle-line text-orange-600 text-xl"></i>
                      </div>
                      <p className="text-sm text-gray-600">
                        Thinking about going long-term? <span className="font-bold text-gray-900 underline decoration-orange-300">Save 20%</span> by switching to the yearly plan.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Pricing Card - Only shown if NOT active, if card is missing, or if explicitly upgrading */}
            {(userData.subscription_status !== 'active' || !userData.stripe_payment_method_id || (showCheckout && selectedPlan === 'yearly')) && (
              <div className="max-w-lg mx-auto">
                <div className="bg-white rounded-2xl shadow-xl overflow-hidden border-2 border-orange-200">
                  <div className="bg-gradient-to-r from-orange-500 to-orange-600 px-6 py-8 text-white text-center">
                    <h3 className="text-2xl font-bold mb-2">{plans[selectedPlan].name}</h3>
                    <div className="flex items-baseline justify-center gap-2">
                      <span className="text-5xl font-bold">${plans[selectedPlan].price}</span>
                      <span className="text-orange-100">/{selectedPlan === 'monthly' ? 'month' : 'year'}</span>
                    </div>
                    {selectedPlan === 'yearly' && (
                      <p className="mt-2 text-orange-100 text-sm">
                        That's just ${(pricing.yearly / 12).toFixed(2)}/month - Save ${(pricing.monthly * 12 - pricing.yearly).toFixed(2)}!
                      </p>
                    )}
                  </div>

                  <div className="p-6">
                    {/* Features */}
                    <div className="space-y-3 mb-6">
                      <div className="flex items-center gap-3">
                        <i className="ri-check-line text-green-600 text-xl"></i>
                        <span className="text-gray-700">Unlimited AI Strategy Analysis</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <i className="ri-check-line text-green-600 text-xl"></i>
                        <span className="text-gray-700">Priority Support</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <i className="ri-check-line text-green-600 text-xl"></i>
                        <span className="text-gray-700">Advanced Analytics Dashboard</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <i className="ri-check-line text-green-600 text-xl"></i>
                        <span className="text-gray-700">Export Reports (PDF, CSV)</span>
                      </div>
                    </div>

                    {/* Error Message */}
                    {paymentError && (
                      <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
                        <p className="text-red-600 text-sm">{paymentError}</p>
                      </div>
                    )}

                    {/* Action Button or Checkout Form */}
                    {!showCheckout ? (
                      <div className="space-y-6">
                        {/* Card Info (Small reminder if card exists) */}
                        {userData.stripe_payment_method_id && userData.subscription_status !== 'active' && (
                          <div className="mt-3 bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs text-blue-700 flex gap-2">
                            <i className="ri-information-line flex-shrink-0"></i>
                            <p>Card on file. Your subscription is secured.</p>
                          </div>
                        )}
                        {/* 3. Subscribe Button */}
                        <button
                          onClick={() => setShowCheckout(true)}
                          className="w-full bg-orange-600 text-white py-4 rounded-xl hover:bg-orange-700 font-bold text-lg shadow-lg shadow-orange-200 transition-all flex items-center justify-center gap-2"
                        >
                          <i className="ri-shield-check-line"></i>
                          {userData.is_beta_user ? 'Confirm Checkout' : `Upgrade to ${plans[selectedPlan].name}`}
                        </button>

                        {/* Security Badge */}
                        <div className="mt-4 text-center">
                          <p className="text-xs text-gray-500 flex items-center justify-center gap-1">
                            <i className="ri-lock-line"></i>
                            Secure payment processing with Stripe
                          </p>
                        </div>
                      </div>
                    ) : (
                      <StripeCheckoutWithSavedCard
                        amount={plans[selectedPlan].price}
                        email={userData.email}
                        name={userData.name}
                        planType={selectedPlan}
                        isBeta={betaStatus?.is_beta_mode || userData?.is_beta_user}
                        onSuccess={handlePaymentSuccess}
                        onError={handlePaymentError}
                        onCancel={() => setShowCheckout(false)}
                      />
                    )}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {activeTab === 'history' && (
          <HistoryView
            history={subscriptionHistory}
            isLoading={isLoadingHistory}
            currentPage={currentPage}
            itemsPerPage={itemsPerPage}
            onPageChange={setCurrentPage}
          />
        )}

        {activeTab === 'manage_card' && (
          <div className="max-w-2xl mx-auto">
            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden p-8">
              <h2 className="text-xl font-bold text-gray-900 mb-6 flex items-center gap-2">
                <i className="ri-bank-card-line text-orange-500"></i>
                Manage Payment Method
              </h2>

              <div className="bg-orange-50 rounded-xl p-4 mb-8 flex gap-3">
                <i className="ri-information-line text-orange-600 mt-1"></i>
                <p className="text-sm text-orange-800">
                  You can update your card details at any time. Your subscription will be billed automatically to the default card on file.
                </p>
              </div>

              <div className="bg-white border-2 border-orange-100 rounded-2xl p-6 shadow-md mb-8">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-4">
                    <div className="w-16 h-16 bg-gray-50 rounded-xl border border-gray-100 flex items-center justify-center">
                      <i className={`ri-${userData.card_brand?.toLowerCase() === 'visa' ? 'visa' : 'mastercard'}-fill text-3xl text-gray-700`}></i>
                    </div>
                    <div>
                      <p className="text-lg font-bold text-gray-900 capitalize">{userData.card_brand} •••• {userData.card_last4}</p>
                      <p className="text-gray-600 text-sm">Expires {userData.card_exp_month}/{userData.card_exp_year}</p>
                    </div>
                  </div>
                  <span className="bg-green-100 text-green-700 text-xs px-3 py-1 rounded-full font-bold">DEFAULT PRIMARY</span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <button
                    onClick={() => {
                      setShowCheckout(true);
                      setActiveTab('plans');
                    }}
                    className="w-full bg-orange-600 text-white py-3 rounded-xl hover:bg-orange-700 transition-all font-bold flex items-center justify-center gap-2"
                  >
                    <i className="ri-edit-line"></i>
                    Update Card
                  </button>
                  <button
                    onClick={async () => {
                      if (window.confirm('Are you sure you want to remove this card? Your subscription might be affected.')) {
                        try {
                          const token = localStorage.getItem('access_token') || localStorage.getItem('auth_token');
                          const res = await fetch('/api/stripe/remove-card', {
                            method: 'POST',
                            headers: { 'Authorization': `Bearer ${token}` }
                          });
                          if (res.ok) {
                            toast.success('Card removed successfully');
                            fetchUserData();
                            setActiveTab('plans');
                          } else {
                            toast.error('Failed to remove card');
                          }
                        } catch (err) {
                          toast.error('An error occurred');
                        }
                      }
                    }}
                    className="w-full bg-white border border-red-200 text-red-600 py-3 rounded-xl hover:bg-red-50 transition-all font-bold flex items-center justify-center gap-2"
                  >
                    <i className="ri-delete-bin-line"></i>
                    Remove Card
                  </button>
                </div>
              </div>

              <div className="text-center">
                <p className="text-xs text-gray-500 font-medium">
                  Lavoo uses Stripe for secure, PCI-compliant payment processing. We never store your full card details.
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function HistoryView({
  history,
  isLoading,
  currentPage,
  itemsPerPage,
  onPageChange
}: {
  history: any[],
  isLoading: boolean,
  currentPage: number,
  itemsPerPage: number,
  onPageChange: (page: number) => void
}) {
  if (isLoading) {
    return (
      <div className="p-12 text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500 mx-auto mb-4"></div>
        <p className="text-gray-600">Loading your history...</p>
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-12 text-center">
        <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <i className="ri-history-line text-gray-400 text-2xl"></i>
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">No payments found</h3>
        <p className="text-gray-600">You haven't made any subscription payments yet.</p>
      </div>
    );
  }

  const totalPages = Math.ceil(history.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedHistory = history.slice(startIndex, startIndex + itemsPerPage);

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
      <div className="p-4 md:p-6 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
        <h2 className="text-lg font-semibold text-gray-900">Subscription History</h2>
        <span className="text-sm text-gray-500">
          Showing {startIndex + 1}-{Math.min(startIndex + itemsPerPage, history.length)} of {history.length}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider">
            <tr>
              <th className="px-6 py-4 font-semibold">Date</th>
              <th className="px-6 py-4 font-semibold">Plan</th>
              <th className="px-6 py-4 font-semibold">Amount</th>
              <th className="px-6 py-4 font-semibold">Reference</th>
              <th className="px-6 py-4 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {paginatedHistory.map((item) => (
              <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  {new Date(item.created_at).toLocaleDateString(undefined, {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric'
                  })}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 capitalize">
                  {item.subscription_plan}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                  ${parseFloat(item.amount).toFixed(2)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 font-mono">
                  {item.tx_ref || (item.transaction_id && item.transaction_id.substring(0, 12) + '...')}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${item.status === 'completed' || item.status === 'active' || item.status === 'succeeded'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-yellow-100 text-yellow-700'
                    }`}>
                    {item.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between">
          <button
            onClick={() => onPageChange(Math.max(1, currentPage - 1))}
            disabled={currentPage === 1}
            className="flex items-center gap-1 px-3 py-1 text-sm font-medium text-gray-600 hover:text-orange-600 disabled:opacity-50 disabled:hover:text-gray-600 transition-colors"
          >
            <i className="ri-arrow-left-s-line text-lg"></i>
            Previous
          </button>

          <div className="flex items-center gap-2">
            {[...Array(totalPages)].map((_, i) => (
              <button
                key={i + 1}
                onClick={() => onPageChange(i + 1)}
                className={`w-8 h-8 rounded-lg text-sm font-medium transition-colors ${currentPage === i + 1
                  ? 'bg-orange-500 text-white'
                  : 'text-gray-600 hover:bg-gray-200'
                  }`}
              >
                {i + 1}
              </button>
            ))}
          </div>

          <button
            onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
            disabled={currentPage === totalPages}
            className="flex items-center gap-1 px-3 py-1 text-sm font-medium text-gray-600 hover:text-orange-600 disabled:opacity-50 disabled:hover:text-gray-600 transition-colors"
          >
            Next
            <i className="ri-arrow-right-s-line text-lg"></i>
          </button>
        </div>
      )}
    </div>
  );
}
