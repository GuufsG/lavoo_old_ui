import { useState } from "react";
import { Outlet } from "react-router-dom";
import DashboardSidebar from "./feature/DashboardSidebar";
import AdminSidebar from "./feature/AdminSidebar";
import Header from "./feature/Header";
// Admin dashboard layout
export function AdminLayout() {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <AdminSidebar
        isMobileMenuOpen={isMobileMenuOpen}
        setIsMobileMenuOpen={setIsMobileMenuOpen}
        isCollapsed={isCollapsed}
        setIsCollapsed={setIsCollapsed}
      />
      {/* Main content area - dynamically adjust margin based on sidebar state */}
      <div className={`flex-1 overflow-y-auto transition-all duration-300 ${isCollapsed ? 'md:ml-16' : 'md:ml-64'
        } ml-0`}>
        <Header onMobileMenuClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)} />
        <Outlet />
      </div>
    </div>
  );
}

import SubscriptionGuard from "./SubscriptionGuard";

// User dashboard layout
export default function DashboardLayout() {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <DashboardSidebar
        isMobileMenuOpen={isMobileMenuOpen}
        setIsMobileMenuOpen={setIsMobileMenuOpen}
        isCollapsed={isCollapsed}
        setIsCollapsed={setIsCollapsed}
      />

      {/* Main content area - dynamically adjust margin based on sidebar state */}
      <div className={`flex-1 overflow-y-auto transition-all duration-300 ${isCollapsed ? 'md:ml-16' : 'md:ml-64'
        } ml-0`}>
        <Header onMobileMenuClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)} />
        <SubscriptionGuard />
      </div>
    </div>
  );
}