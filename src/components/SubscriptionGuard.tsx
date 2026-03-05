import { useEffect, useState } from 'react';
import { useLocation, useNavigate, Outlet } from 'react-router-dom';
import { useCurrentUser, useBetaStatus } from '../api/user';
import Button from './base/Button';

const ALLOWED_PATHS = ['/dashboard', '/dashboard/profile', '/dashboard/upgrade'];

export default function SubscriptionGuard() {
    const { data: user, isLoading: isUserLoading } = useCurrentUser();
    const { data: betaStatus, isLoading: isBetaLoading } = useBetaStatus();
    const location = useLocation();
    const navigate = useNavigate();
    const [showModal, setShowModal] = useState(false);

    useEffect(() => {
        if (isUserLoading || isBetaLoading) return;

        // Check if the user is currently restricted
        // Restricted if: user is logged in, their status requires action/paused due to expiration,
        // and they don't have an active subscription or card saved (depending on beta status logic).
        // The betaStatus API response provides `status`
        if (betaStatus) {
            // Beta users have complete access: any status starting with "beta_" should NOT be restricted
            const isBetaStatus = betaStatus.status?.startsWith('beta_');

            // Restricted statuses that require a subscription or saved card
            // grace_no_card is NOT restricted; users are allowed access during their grace period.
            const restrictedStatuses = ['grace_expired_no_card', 'new_user'];

            const isRestrictedByStatus = restrictedStatuses.includes(betaStatus.status);
            const hasNoActiveSubscription = !user?.subscription_status ||
                (user.subscription_status !== 'active' && user.subscription_status !== 'trialing');

            const isRestricted = !isBetaStatus && isRestrictedByStatus && hasNoActiveSubscription;

            const isPathAllowed = ALLOWED_PATHS.some(path =>
                location.pathname === path ||
                location.pathname.startsWith('/dashboard/upgrade') ||
                location.pathname.startsWith('/dashboard/profile')
            );

            if (isRestricted && !isPathAllowed) {
                setShowModal(true);
            } else {
                setShowModal(false);
            }
        }
    }, [user, betaStatus, location, isUserLoading, isBetaLoading, navigate]);

    if (isUserLoading || isBetaLoading) {
        return (
            <div className="flex h-screen items-center justify-center bg-gray-50">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-orange-500"></div>
            </div>
        );
    }

    return (
        <>
            <Outlet />
            {showModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4">
                    <div className="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-200">
                        <div className="p-6 text-center">
                            <div className="w-16 h-16 bg-orange-100 text-orange-600 rounded-full flex items-center justify-center mx-auto mb-4">
                                <i className="ri-lock-line text-2xl"></i>
                            </div>
                            <h3 className="text-xl font-bold text-gray-900 mb-2">
                                Checkout Required
                            </h3>
                            <p className="text-sm text-gray-600 mb-6">
                                Please subscribe or save your card to experience Lavoo's business diagnostic capabilities.
                            </p>

                            <div className="flex flex-col gap-3">
                                <Button
                                    variant="primary"
                                    className="w-full justify-center"
                                    onClick={() => {
                                        setShowModal(false);
                                        navigate('/dashboard/upgrade');
                                    }}
                                >
                                    Checkout Now
                                </Button>

                                <Button
                                    variant="outline"
                                    className="w-full justify-center"
                                    onClick={() => {
                                        setShowModal(false);
                                        navigate('/dashboard');
                                    }}
                                >
                                    Return to Dashboard
                                </Button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}
