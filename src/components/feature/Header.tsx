import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import Cookies from "js-cookie";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";

const timeAgo = (date: Date) => {
  const seconds = Math.floor((new Date().getTime() - date.getTime()) / 1000);
  let interval = seconds / 31536000;
  if (interval > 1) return Math.floor(interval) + " years ago";
  interval = seconds / 2592000;
  if (interval > 1) return Math.floor(interval) + " months ago";
  interval = seconds / 86400;
  if (interval > 1) return Math.floor(interval) + " days ago";
  interval = seconds / 3600;
  if (interval > 1) return Math.floor(interval) + " hours ago";
  interval = seconds / 60;
  if (interval > 1) return Math.floor(interval) + " mins ago";
  return Math.floor(seconds) + " secs ago";
};

import Button from "../base/Button";
import { useCurrentUser, useBetaStatus } from "../../api/user";

function InlineCountdown({ endsAt }: { endsAt: string }) {
  const [timeLeft, setTimeLeft] = useState<{ d: number; h: number; m: number; s: number } | null>(null);

  useEffect(() => {
    const calc = () => {
      const diff = new Date(endsAt).getTime() - new Date().getTime();
      if (diff <= 0) {
        setTimeLeft({ d: 0, h: 0, m: 0, s: 0 });
        return;
      }
      setTimeLeft({
        d: Math.floor(diff / (1000 * 60 * 60 * 24)),
        h: Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60)),
        m: Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60)),
        s: Math.floor((diff % (1000 * 60)) / 1000),
      });
    };
    calc();
    const timer = setInterval(calc, 1000);
    return () => clearInterval(timer);
  }, [endsAt]);

  if (!timeLeft) return null;

  const pad = (n: number) => n.toString().padStart(2, '0');

  return (
    <span className="font-mono font-black ml-2 bg-white/20 rounded px-2 py-0.5 text-white text-sm tracking-widest">
      {timeLeft.d > 0 && <>{timeLeft.d}d&nbsp;</>}
      {pad(timeLeft.h)}:{pad(timeLeft.m)}:{pad(timeLeft.s)}
    </span>
  );
}

interface HeaderProps {
  onMobileMenuClick?: () => void;
}

interface Notification {
  id: string;
  source: "system" | "alert";
  type: string;
  title: string;
  message: string;
  created_at: string;
  read: boolean;
  link?: string;
}

export default function Header({ onMobileMenuClick }: HeaderProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [, setIsMobileMenuOpen] = useState(false);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const queryClient = useQueryClient();
  const notificationRef = useRef<HTMLDivElement>(null);

  const { data: user, isLoading } = useCurrentUser();
  const { data: betaStatus } = useBetaStatus();

  const isLoggedIn = !!user;
  const isAnalyzePage = location.pathname === "/analyze";
  const isLoginPage = location.pathname === "/login";
  const isSignUpPage = location.pathname === "/signup";
  const isDashboard = location.pathname.includes("dashboard") ||
    location.pathname.includes("/results") ||
    location.pathname.includes("/analysis-history");
  const isAdmin = location.pathname.includes("/admin");

  // Derived state for unread count
  const unreadCount = notifications.filter(n => !n.read).length;

  const handleLogout = () => {
    // Clear the cookies
    Cookies.remove("access_token");
    Cookies.remove("auth_token");
    Cookies.remove("user_token");

    // Clear local storage
    localStorage.clear();

    // Invalidate queries
    queryClient.invalidateQueries({ queryKey: ["currentUser"] });
    queryClient.removeQueries({ queryKey: ["currentUser"] });

    // Navigate and force state reset
    navigate("/login", { replace: true });
    window.location.reload(); // Force full reload to clear all in-memory states
  };

  const scrollToSection = (sectionId: string) => {
    if (location.pathname !== "/") {
      navigate("/");
      setTimeout(() => {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
      }, 100);
    } else {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
    }
    setIsMobileMenuOpen(false);
  };

  const handleNotificationClick = async () => {
    // Open/close immediately for better UX
    const willOpen = !isNotificationsOpen;
    setIsNotificationsOpen(willOpen);

    if (willOpen) {
      // Mark all as read on backend in background
      try {
        const token = Cookies.get("access_token") || localStorage.getItem("auth_token") || localStorage.getItem("user_token") || localStorage.getItem("access_token");
        await axios.post("/api/notifications/read-all", {}, {
          headers: token ? { Authorization: `Bearer ${token}` } : {}
        });
        // Update local state
        setNotifications(prev => prev.map(n => ({ ...n, read: true })));
      } catch (err) {
        console.error("Failed to mark all as read:", err);
      }
    }
  };

  // Fetch initial notifications
  const fetchNotifications = async () => {
    if (!isLoggedIn) return;
    try {
      const token = Cookies.get("access_token") || localStorage.getItem("auth_token") || localStorage.getItem("user_token") || localStorage.getItem("access_token");
      const res = await axios.get("/api/notifications", {
        headers: token ? { Authorization: `Bearer ${token}` } : {}
      });
      setNotifications(res.data.notifications || []);
    } catch (err) {
      console.error("Failed to fetch notifications:", err);
    }
  };

  useEffect(() => {
    if (isLoggedIn) {
      fetchNotifications();
    }
  }, [isLoggedIn]);

  // WebSocket for real-time notifications
  useEffect(() => {
    if (!isLoggedIn) return;

    const token = Cookies.get("access_token") || localStorage.getItem("auth_token") || localStorage.getItem("user_token") || localStorage.getItem("access_token");
    if (!token) {
      console.warn("No auth token available for WebSocket connection");
      return;
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/api/customer-service/ws/notifications?token=${token}`;

    console.log("Attempting to connect to notifications WS...");

    let ws: WebSocket | null = null;
    let reconnectTimeout: NodeJS.Timeout | null = null;
    let isIntentionallyClosed = false;

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log("✓ Notifications WS connected");
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === "new_notification") {
              const newNotif = data.payload;
              setNotifications((prev) => [
                {
                  id: `notif_${newNotif.id}`,
                  source: "system",
                  type: newNotif.type,
                  title: newNotif.title,
                  message: newNotif.message,
                  created_at: newNotif.created_at,
                  read: newNotif.is_read,
                  link: newNotif.link
                },
                ...prev
              ]);
            } else if (data.type === "alert_notification") {
              fetchNotifications();
            }
          } catch (err) {
            console.error("WS message parse error:", err);
          }
        };

        ws.onerror = (err) => {
          console.error("WS Error occurred:", err);
        };

        ws.onclose = (event) => {
          console.log(`WS Connection closed. Code: ${event.code}, Reason: ${event.reason || 'No reason provided'}`);

          // Only attempt reconnect if not intentionally closed and logged in
          if (!isIntentionallyClosed && isLoggedIn) {
            console.log("Attempting to reconnect in 5 seconds...");
            reconnectTimeout = setTimeout(() => {
              connect();
            }, 5000);
          }
        };
      } catch (error) {
        console.error("Failed to create WebSocket connection:", error);
      }
    };

    // Initial connection
    connect();

    return () => {
      isIntentionallyClosed = true;
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
      }
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close();
      }
    };
  }, [isLoggedIn]);

  // Close notifications when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (notificationRef.current && !notificationRef.current.contains(event.target as Node)) {
        setIsNotificationsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);


  if (!isAdmin)
    return (
      <header className="bg-white border-b border-gray-200 sticky top-0 z-[110]">
        {betaStatus && (betaStatus.status === 'beta_no_card' || betaStatus.status === 'grace_no_card' || betaStatus.status === 'new_user') && (
          <div className="py-2 px-4 text-center text-sm font-medium transition-colors bg-orange-600 text-white shadow-sm">
            <div className="max-w-7xl mx-auto flex items-center justify-center space-x-2 flex-wrap gap-y-2">
              <span>🎁 {betaStatus.message}</span>
              {betaStatus.countdown_ends_at && <InlineCountdown endsAt={betaStatus.countdown_ends_at} />}
              <button
                onClick={() => navigate('/dashboard/upgrade')}
                className="ml-2 px-3 py-1 bg-white text-orange-600 rounded-full text-xs font-bold hover:bg-orange-50 transition-colors"
              >
                {betaStatus.is_beta_user ? 'Save Card' : 'Subscribe'}
              </button>
            </div>
          </div>
        )}
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className={`flex justify-between items-center h-16 sm:h-20 ${isDashboard ? "!justify-end w-full" : ""}`}>
            {/* Logo */}
            {/* Mobile Menu Button - Show hamburger in dashboard */}
            {isDashboard ? (
              <button
                className="md:hidden p-2 text-gray-700 hover:text-orange-500 transition-colors"
                onClick={onMobileMenuClick}
              >
                <i className="ri-menu-line text-2xl"></i>
              </button>
            ) : (
              /* Logo for non-dashboard pages */
              <div
                className="flex items-center cursor-pointer"
                onClick={() => {
                  if (isDashboard || isLoginPage || isSignUpPage) return;
                  navigate("/");
                }}
              >
                <img
                  src="/logo.png"
                  alt="Lavoo"
                  className="h-[150px] sm:h-[200px] w-auto object-contain"
                />
                {/* Removed text and icon as requested to replace with logo image */}
              </div>
            )}

            {/* Desktop Navigation */}
            <nav className="hidden md:flex items-center space-x-6 lg:space-x-8">
              {!isLoggedIn && !isDashboard && location.pathname !== '/' && (
                <>
                  <button
                    onClick={() => navigate("/")}
                    className="text-gray-700 hover:text-orange-500 font-medium transition-colors whitespace-nowrap"
                  >
                    Home
                  </button>
                </>
              )}

              {/* Dashboard navigation for logged-in users */}
              {isLoggedIn && isDashboard && !isAdmin && (
                <>
                  {/* Notification Bell */}
                  <div className="relative" ref={notificationRef}>
                    <button
                      onClick={handleNotificationClick}
                      className="p-2 text-gray-600 hover:text-orange-500 transition-colors relative"
                    >
                      <i className="ri-notification-3-line text-xl"></i>
                      {unreadCount > 0 && (
                        <span className="absolute top-0 right-0 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-bold leading-none text-white transform translate-x-1/4 -translate-y-1/4 bg-red-500 rounded-full">
                          {unreadCount}
                        </span>
                      )}
                    </button>

                    {/* Notification Popover */}
                    {isNotificationsOpen && (
                      <div className="absolute left-1/2 -translate-x-1/2 md:right-0 md:left-auto md:translate-x-0 mt-2 w-80 bg-white border border-gray-200 rounded-lg shadow-xl overflow-hidden z-50 animate-in fade-in zoom-in-95 duration-200 origin-top">
                        <div className="px-4 py-3 border-b border-gray-100 flex justify-between items-center bg-gray-50">
                          <h3 className="font-semibold text-gray-900">Notifications</h3>
                          {/* <span className="text-xs text-gray-500">Mark all as read</span> */}
                        </div>
                        <div className="max-h-96 overflow-y-auto">
                          {notifications.length === 0 ? (
                            <div className="px-4 py-6 text-center text-gray-500 text-sm">
                              No notifications
                            </div>
                          ) : (
                            <div className="divide-y divide-gray-100">
                              {notifications.map((notification) => (
                                <div
                                  key={notification.id}
                                  className={`px-4 py-3 hover:bg-gray-50 cursor-pointer transition-colors ${!notification.read ? 'bg-orange-50/30' : ''}`}
                                  onClick={() => notification.link && navigate(notification.link)}
                                >
                                  <div className="flex items-start">
                                    <div className="flex-shrink-0 mt-1">
                                      <div className={`w-8 h-8 rounded-full flex items-center justify-center ${notification.source === 'alert' ? 'bg-orange-100 text-orange-600' : 'bg-blue-100 text-blue-600'}`}>
                                        <i className={notification.source === 'alert' ? "ri-alert-line text-sm" : "ri-information-line text-sm"}></i>
                                      </div>
                                    </div>
                                    <div className="ml-3 w-0 flex-1">
                                      <p className="text-sm font-medium text-gray-900">{notification.title}</p>
                                      <p className="mt-1 text-sm text-gray-500 line-clamp-2">{notification.message}</p>
                                      <p className="mt-1 text-xs text-gray-400">
                                        {timeAgo(new Date(notification.created_at))}
                                      </p>
                                    </div>
                                    {!notification.read && (
                                      <div className="flex-shrink-0 ml-2">
                                        <div className="w-2 h-2 bg-orange-500 rounded-full"></div>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        <div className="px-4 py-2 border-t border-gray-100 bg-gray-50">
                          <button className="text-xs text-orange-600 hover:text-orange-700 font-medium w-full text-center">
                            View all notifications
                          </button>
                        </div>
                      </div>
                    )}
                  </div>


                  <button
                    onClick={() => scrollToSection("features")}
                    className="text-gray-700 hover:text-orange-500 font-medium transition-colors whitespace-nowrap"
                  >
                    <i className="ri-play-circle-line mr-2"></i>
                    Watch a Demo
                  </button>

                </>
              )}

              {/* Show login/signup if NOT logged in and NOT on dashboard */}
              {!isAnalyzePage && !isLoggedIn && !isDashboard && (
                <>
                  {(!isLoginPage || isAdmin) && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => navigate("/login")}
                      className="whitespace-nowrap"
                    >
                      Log In
                    </Button>
                  )}
                  {(!isSignUpPage || isAdmin) && (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => navigate("/signup")}
                      className="whitespace-nowrap"
                    >
                      Sign Up
                    </Button>
                  )}
                </>
              )}

              {/* Show user dropdown if logged in */}
              {isLoggedIn && !isLoading && (
                <div className="relative">
                  <button
                    onClick={() => setIsDropdownOpen((prev) => !prev)}
                    className="flex items-center space-x-2 bg-gray-100 px-3 py-1.5 rounded-lg hover:bg-gray-200 transition"
                  >
                    <i className="ri-user-line text-gray-700"></i>
                    <span className="font-medium text-gray-800">
                      {user?.name || user?.email}
                    </span>
                    <i
                      className={`ri-arrow-down-s-line transition-transform ${isDropdownOpen ? "rotate-180" : ""
                        }`}
                    ></i>
                  </button>

                  {isDropdownOpen && (
                    <div className="absolute right-0 mt-2 w-40 bg-white border rounded-lg shadow-md py-2">
                      <button
                        onClick={handleLogout}
                        className="block w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-100"
                      >
                        Logout
                      </button>
                      <div>
                        <button className="block w-full text-left px-4 py-2 text-gray-700 hover:bg-gray-100"
                          onClick={() => navigate('/dashboard/profile')}>
                          Profile
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </nav>

            {/* Mobile Menu Button */}
            {/* <button
            className="md:hidden p-2 text-gray-700 hover:text-orange-500 transition-colors"
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          >
            <i
              className={`${
                isMobileMenuOpen ? "ri-close-line" : "ri-menu-line"
              } text-2xl`}
            ></i>
          </button> */}
          </div>
        </div>
      </header>
    );

  return null;
}

