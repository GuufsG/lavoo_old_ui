import { useNavigate } from 'react-router-dom';
import Button from '@/components/base/Button';
import { useCurrentUser, useUserChops } from '@/api/user';
import {
    useDashboardStats,
    useUrgentAlerts,
    useRecentReviews,
    useRecentAnalyses,
} from '@/api/dashboard';

function Dashboard() {
    const navigate = useNavigate();

    // Use TanStack Query hooks for all data fetching with caching
    const { data: currentUser, isLoading: isLoadingUser } = useCurrentUser();
    const { isLoading: isLoadingChops } = useUserChops();
    const userId = currentUser?.id || null;

    // Determine user subscription status
    const alertsLimit = 3; // Always show 3 alerts on dashboard widget (free users get 5 total on alerts page)

    const { data: stats, isLoading: isLoadingStats } = useDashboardStats(userId);
    const { data: urgentAlerts = [], isLoading: isLoadingAlerts } = useUrgentAlerts(userId, alertsLimit);
    const { data: recentAnalyses = [] } = useRecentAnalyses();
    const { data: recentReviews = [], isLoading: isLoadingReviews } = useRecentReviews();

    // Combined loading state
    const loading = isLoadingUser || isLoadingChops || isLoadingStats || isLoadingAlerts || isLoadingReviews;

    const formatTimeAgo = (dateString: string) => {
        const date = new Date(dateString);
        const now = new Date();
        const diffInHours = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60));

        if (diffInHours < 1) return 'Just now';
        if (diffInHours < 24) return `${diffInHours} hours ago`;
        const diffInDays = Math.floor(diffInHours / 24);
        if (diffInDays === 1) return '1 day ago';
        return `${diffInDays} days ago`;
    };

    // Loading state
    if (loading) {
        return (
            <div className="flex-1 flex items-center justify-center min-h-screen bg-gradient-to-br from-orange-50 to-white">
                <div className="text-center">
                    <div className="w-12 h-12 border-4 border-orange-200 border-t-orange-600 rounded-full animate-spin mx-auto mb-4"></div>
                    <p className="text-gray-600">Loading dashboard...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex-1 flex flex-col min-h-screen bg-gradient-to-br from-orange-50 to-white px-4">
            <div className="flex-1 p-4 md:p-6 lg:p-8">
                {/* Header */}
                <div className="mb-6 md:mb-8">
                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-4 sm:space-y-0">
                        <div className="flex-1">
                            <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-gray-900 mb-2">
                                Welcome to Lavoo
                            </h1>
                            {currentUser?.is_beta_user && (currentUser.app_mode === 'beta' || !currentUser.stripe_payment_method_id) && (
                                <div className={`mb-4 p-4 rounded-xl border ${!currentUser.stripe_payment_method_id
                                    ? 'bg-orange-50 border-orange-200 text-orange-800'
                                    : 'bg-green-50 border-green-200 text-green-800'
                                    }`}>
                                    <div className="flex items-start space-x-3">
                                        <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${!currentUser.stripe_payment_method_id ? 'bg-orange-100' : 'bg-green-100'
                                            }`}>
                                            <i className={!currentUser.stripe_payment_method_id ? "ri-gift-line text-orange-600" : "ri-checkbox-circle-line text-green-600"}></i>
                                        </div>
                                        <div>
                                            <h3 className="font-bold">
                                                {!currentUser.stripe_payment_method_id ? "🎁 Legacy User Bonus" : "✅ Launch Ready"}
                                            </h3>
                                            <p className="text-sm">
                                                {!currentUser.stripe_payment_method_id
                                                    ? currentUser.days_remaining !== undefined && currentUser.days_remaining !== null
                                                        ? `Secure your access to Lavoo. You have ${currentUser.days_remaining} day${currentUser.days_remaining === 1 ? '' : 's'} remaining in your grace period. Checkout today!`
                                                        : "Secure your access to Lavoo. Checkout today!"
                                                    : "You're all set! Your access is secured."}
                                            </p>
                                            {!currentUser.stripe_payment_method_id && (
                                                <Button
                                                    onClick={() => navigate('/dashboard/upgrade')}
                                                    size="sm"
                                                    className="mt-3 bg-orange-600 hover:bg-orange-700 text-white border-none"
                                                >
                                                    Checkout
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            )}
                            <p className="text-gray-600 text-sm md:text-base">
                                Get a complete overview of your business performance and access all tools from here.
                            </p>
                        </div>
                    </div>
                </div>

                {/* Quick Stats */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6 md:mb-8">
                    {/* Total Analyses */}
                    <div className="bg-white p-4 md:p-6 rounded-xl border border-gray-100 shadow-sm hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <div>
                                <p className="text-sm text-gray-600 mb-1">Total Analyses</p>
                                <p className="text-xl md:text-2xl font-bold text-gray-900">{stats?.total_analyses || 0}</p>
                            </div>
                            <div className="w-8 h-8 md:w-10 md:h-10 rounded-lg bg-blue-50 flex items-center justify-center">
                                <i className="ri-line-chart-line text-blue-600"></i>
                            </div>
                        </div>
                    </div>

                    {/* Active Alerts */}
                    <div className="bg-white rounded-xl p-4 md:p-6 shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="flex items-center justify-between">
                            <div className="flex-1">
                                <p className="text-sm text-gray-600 mb-1">Active Alerts</p>
                                <p className="text-xl md:text-2xl font-bold text-gray-900">{stats?.active_alerts || 0}</p>
                            </div>
                            <div className="w-10 h-10 md:w-12 md:h-12 bg-orange-100 rounded-lg flex items-center justify-center flex-shrink-0 ml-3">
                                <i className="ri-alert-line text-orange-600 text-lg md:text-xl"></i>
                            </div>
                        </div>
                    </div>

                    {/* Commissions */}
                    <div className="bg-white rounded-xl p-4 md:p-6 shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="flex items-center justify-between">
                            <div className="flex-1">
                                <p className="text-sm text-gray-600 mb-1">Total Commissions</p>
                                <p className="text-xl md:text-2xl font-bold text-gray-900">
                                    ${(stats?.total_commissions || 0).toLocaleString()}
                                </p>
                            </div>
                            <div className="w-10 h-10 md:w-12 md:h-12 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0 ml-3">
                                <i className="ri-wallet-3-line text-purple-600 text-lg md:text-xl"></i>
                            </div>
                        </div>
                    </div>

                    {/* Total Referrals */}
                    <div className="bg-white rounded-xl p-4 md:p-6 shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="flex items-center justify-between">
                            <div className="flex-1">
                                <p className="text-sm text-gray-600 mb-1">Total Referrals</p>
                                <p className="text-xl md:text-2xl font-bold text-gray-900">{stats?.total_referrals || 0}</p>
                            </div>
                            <div className="w-10 h-10 md:w-12 md:h-12 bg-green-100 rounded-lg flex items-center justify-center flex-shrink-0 ml-3">
                                <i className="ri-group-line text-green-600 text-lg md:text-xl"></i>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Dashboard Sections */}
                <div className="space-y-6 md:space-y-8">
                    {/* Decision Engine Section */}
                    <div className="bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="p-4 md:p-6 border-b border-gray-200">
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
                                <div className="flex items-center space-x-3">
                                    <div className="w-8 h-8 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0">
                                        <i className="ri-search-line text-purple-600"></i>
                                    </div>
                                    <h3 className="text-xl md:text-2xl font-semibold text-gray-900">Decision Engine</h3>
                                </div>
                                <Button
                                    onClick={() => navigate('/dashboard/analyze')}
                                    variant="outline"
                                    size="sm"
                                    className="whitespace-nowrap"
                                >
                                    Go To <i className="ri-arrow-right-line ml-1"></i>
                                </Button>
                            </div>
                        </div>
                        <div className="p-4 md:p-6">
                            {recentAnalyses.length > 0 ? (
                                <div className={`grid grid-cols-1 ${recentAnalyses.length === 1 ? 'lg:grid-cols-1' : recentAnalyses.length === 2 ? 'md:grid-cols-2' : 'lg:grid-cols-3'} gap-4 md:gap-6`}>
                                    {recentAnalyses.slice(0, 3).map((analysis) => (
                                        <div key={analysis.analysis_id} className="border border-gray-200 rounded-lg p-4 md:p-5 hover:border-purple-200 hover:bg-purple-50/30 transition-all duration-200 cursor-default">
                                            <div className="flex items-center justify-between mb-3">
                                                <span className="text-xs px-2 py-1 rounded-full font-medium bg-purple-100 text-purple-600">
                                                    Analysis Result
                                                </span>
                                            </div>
                                            <h4 className="font-medium text-gray-900 mb-2 truncate text-base md:text-lg">
                                                {analysis.business_goal}
                                            </h4>
                                            <p className="text-base text-gray-600 leading-relaxed line-clamp-2">
                                                {analysis.primary_bottleneck?.title || "Analysis completed"}
                                            </p>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="flex flex-col items-center justify-center py-12 text-center bg-gray-50 rounded-lg border border-dashed border-gray-200">
                                    <div className="w-16 h-16 bg-purple-100 rounded-full flex items-center justify-center mb-4">
                                        <i className="ri-magic-line text-purple-600 text-2xl"></i>
                                    </div>
                                    <p className="text-lg font-medium text-gray-900 max-w-md">
                                        Experience Lavoo's cutting edge solutions to your business constraints
                                    </p>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Opportunity Alerts Section */}
                    <div className="bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="p-4 md:p-6 border-b border-gray-200">
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
                                <div className="flex items-center space-x-3">
                                    <div className="w-8 h-8 bg-orange-100 rounded-lg flex items-center justify-center flex-shrink-0">
                                        <i className="ri-alert-line text-orange-600"></i>
                                    </div>
                                    <h3 className="text-xl md:text-2xl font-semibold text-gray-900">Opportunity Alerts</h3>
                                </div>
                                <Button
                                    onClick={() => navigate('/dashboard/alerts')}
                                    variant="outline"
                                    size="sm"
                                    className="whitespace-nowrap"
                                >
                                    View All <i className="ri-arrow-right-line ml-1"></i>
                                </Button>
                            </div>
                        </div>
                        <div className="p-4 md:p-6">
                            {urgentAlerts.length > 0 ? (
                                <div className="space-y-4">
                                    {urgentAlerts.map((alert) => (
                                        <div key={alert.id} className="border border-gray-200 rounded-lg p-4 md:p-6 hover:border-orange-200 hover:bg-orange-50/30 transition-all duration-200 cursor-pointer"
                                            onClick={() => navigate('/dashboard/alerts')}>
                                            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between space-y-3 sm:space-y-0 mb-3">
                                                <div className="flex items-center space-x-2">
                                                    <div className="w-6 h-6 bg-red-100 rounded-full flex items-center justify-center flex-shrink-0">
                                                        <i className="ri-fire-line text-red-600 text-sm"></i>
                                                    </div>
                                                    <span className="text-xs bg-red-100 text-red-600 px-2 py-1 rounded-full font-medium">
                                                        {alert.priority}
                                                    </span>
                                                </div>
                                                <div className="text-left sm:text-right">
                                                    <div className="text-sm font-medium text-gray-900">Score: {alert.score}</div>
                                                    <div className="text-xs text-gray-500">{alert.time_remaining}</div>
                                                </div>
                                            </div>
                                            <h4 className="font-medium text-gray-900 mb-2 text-base md:text-lg">{alert.title}</h4>
                                            <p className="text-base text-gray-600 leading-relaxed line-clamp-2">{alert.why_act_now}</p>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <p className="text-center text-gray-500 py-8">No urgent alerts at the moment</p>
                            )}
                        </div>
                    </div>

                    {/* Earnings Overview Section */}
                    <div className="bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                        <div className="p-4 md:p-6 border-b border-gray-200">
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
                                <div className="flex items-center space-x-3">
                                    <div className="w-8 h-8 bg-green-100 rounded-lg flex items-center justify-center flex-shrink-0">
                                        <i className="ri-money-dollar-circle-line text-green-600"></i>
                                    </div>
                                    <h3 className="text-xl md:text-2xl font-semibold text-gray-900">Earnings Overview</h3>
                                </div>
                                <Button
                                    onClick={() => navigate('/dashboard/earnings')}
                                    variant="outline"
                                    size="sm"
                                    className="whitespace-nowrap"
                                >
                                    View All <i className="ri-arrow-right-line ml-1"></i>
                                </Button>
                            </div>
                        </div>
                        <div className="p-4 md:p-6">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
                                {/* Total Commissions */}
                                <div className="border border-gray-200 rounded-lg p-4 md:p-5 hover:border-purple-200 hover:bg-purple-50/30 transition-all duration-200">
                                    <div className="flex items-center justify-between mb-3">
                                        <h4 className="font-medium text-gray-900 text-sm md:text-base">Total Commissions</h4>
                                        <i className="ri-wallet-3-line text-purple-600 text-xl"></i>
                                    </div>
                                    <div className="text-xl md:text-2xl font-bold text-gray-900 mb-1">
                                        ${(stats?.total_commissions || 0).toLocaleString()}
                                    </div>
                                    <div className="text-sm text-gray-500">All recorded commissions</div>
                                </div>

                                {/* Paid Commissions */}
                                <div className="border border-gray-200 rounded-lg p-4 md:p-5 hover:border-green-200 hover:bg-green-50/30 transition-all duration-200">
                                    <div className="flex items-center justify-between mb-3">
                                        <h4 className="font-medium text-gray-900 text-sm md:text-base">Paid Commissions</h4>
                                        <i className="ri-check-double-line text-green-600 text-xl"></i>
                                    </div>
                                    <div className="text-xl md:text-2xl font-bold text-gray-900 mb-1">
                                        ${(stats?.paid_commissions || 0).toLocaleString()}
                                    </div>
                                    <div className="text-sm text-gray-500">Successfully paid out</div>
                                </div>

                                {/* Total Referrals */}
                                <div className="border border-gray-200 rounded-lg p-4 md:p-5 hover:border-blue-200 hover:bg-blue-50/30 transition-all duration-200">
                                    <div className="flex items-center justify-between mb-3">
                                        <h4 className="font-medium text-gray-900 text-sm md:text-base">Total Referrals</h4>
                                        <i className="ri-group-line text-blue-600 text-xl"></i>
                                    </div>
                                    <div className="text-xl md:text-2xl font-bold text-gray-900 mb-1">{stats?.total_referrals || 0}</div>
                                    <div className="text-sm text-green-500 font-medium">
                                        {(stats?.referrals_this_month || 0) > 0 ? `+${stats?.referrals_this_month} this month` : 'Start referring!'}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Recent Reviews Section */}
                    {recentReviews.length > 0 && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md transition-shadow duration-200">
                            <div className="p-4 md:p-6 border-b border-gray-200">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
                                    <div className="flex items-center space-x-3">
                                        <div className="w-8 h-8 bg-yellow-100 rounded-lg flex items-center justify-center flex-shrink-0">
                                            <i className="ri-star-line text-yellow-600"></i>
                                        </div>
                                        <h3 className="text-xl md:text-2xl font-semibold text-gray-900">Recent Reviews</h3>
                                    </div>
                                    <Button
                                        onClick={() => navigate('/dashboard/reviews')}
                                        variant="outline"
                                        size="sm"
                                        className="whitespace-nowrap"
                                    >
                                        View All <i className="ri-arrow-right-line ml-1"></i>
                                    </Button>
                                </div>
                            </div>
                            <div className="p-4 md:p-6">
                                <div className="space-y-4">
                                    {recentReviews.map((review) => (
                                        <div key={review.id} className="border border-gray-200 rounded-lg p-4 md:p-5 hover:border-yellow-200 hover:bg-yellow-50/30 transition-all duration-200">
                                            <div className="flex items-start justify-between mb-2">
                                                <h4 className="font-medium text-gray-900">{review.business_name}</h4>
                                                <div className="flex items-center">
                                                    {[...Array(5)].map((_, i) => (
                                                        <i key={i} className={`ri-star-${i < review.rating ? 'fill' : 'line'} text-yellow-500 text-sm`}></i>
                                                    ))}
                                                </div>
                                            </div>
                                            <p className="text-sm text-gray-600 mb-2 line-clamp-2">{review.review_text}</p>
                                            <p className="text-xs text-gray-500">{formatTimeAgo(review.date_submitted)}</p>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export default Dashboard;
