import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useEffect } from "react";

export interface User {
  id: number;
  email: string;
  name?: string;
  subscription_status?: string;
  subscription_plan?: string;
  referral_code?: string;
  total_chops?: number;
  created_at?: string;
  department?: string;
  location?: string;
  bio?: string;
  is_beta_user?: boolean;
  beta_joined_at?: string;
  grace_period_ends_at?: string;
  stripe_customer_id?: string;
  stripe_payment_method_id?: string;
  card_last4?: string;
  card_brand?: string;
  card_exp_month?: number;
  card_exp_year?: number;
  card_saved_at?: string;
  app_mode?: string;
  days_remaining?: number;
  countdown_ends_at?: string;
}

export const useCurrentUser = () => {
  return useQuery({
    queryKey: ["currentUser"],
    queryFn: async () => {
      const token = localStorage.getItem("auth_token") || localStorage.getItem("user_token") || localStorage.getItem("access_token");
      if (!token) return null;

      try {
        const res = await axios.get("/api/me", {
          withCredentials: true,
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        return res.data;
      } catch (err: any) {
        if (err.response?.status === 401) {
          try {
            await axios.post(
              "/api/refresh",
              {},
              { withCredentials: true }
            );

            const retryRes = await axios.get("/api/me", {
              withCredentials: true,
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            return retryRes.data
          } catch (refreshErr) {
            throw new Error("Session expired. Please login again.");
          }
        } else {
          throw err
        }
      }
    },
  });
};

export const updateProfile = async (data: any) => {
  const token = localStorage.getItem("access_token");
  const res = await axios.patch("/api/me", data, {
    withCredentials: true,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return res.data;
};

// --- CHOPS MANAGEMENT ---

export interface UserChops {
  total_chops: number;
  alert_reading_chops: number;
  alert_sharing_chops: number;
  insight_reading_chops: number;
  insight_sharing_chops: number;
  referral_chops: number;
  referral_count: number;
}

export const useUserChops = () => {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["userChops"],
    queryFn: async (): Promise<UserChops> => {
      const token = localStorage.getItem("auth_token") || localStorage.getItem("user_token") || localStorage.getItem("access_token");
      if (!token) throw new Error("No auth token");

      try {
        const res = await axios.get("/api/users/me", {
          withCredentials: true,
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });

        const userData = res.data;
        return {
          total_chops: userData.total_chops || 0,
          alert_reading_chops: userData.alert_reading_chops || 0,
          alert_sharing_chops: userData.alert_sharing_chops || 0,
          insight_reading_chops: userData.insight_reading_chops || 0,
          insight_sharing_chops: userData.insight_sharing_chops || 0,
          referral_chops: userData.referral_chops || 0,
          referral_count: userData.referral_count || 0,
        };
      } catch (err: any) {
        if (err.response?.status === 401) {
          try {
            await axios.post(
              "/api/refresh",
              {},
              { withCredentials: true }
            );

            const retryRes = await axios.get("/api/users/me", {
              withCredentials: true,
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });

            const userData = retryRes.data;
            return {
              total_chops: userData.total_chops || 0,
              alert_reading_chops: userData.alert_reading_chops || 0,
              alert_sharing_chops: userData.alert_sharing_chops || 0,
              insight_reading_chops: userData.insight_reading_chops || 0,
              insight_sharing_chops: userData.insight_sharing_chops || 0,
              referral_chops: userData.referral_chops || 0,
              referral_count: userData.referral_count || 0,
            };
          } catch (refreshErr) {
            throw new Error("Session expired. Please login again.");
          }
        } else {
          throw err;
        }
      }
    },
    enabled: !!localStorage.getItem("auth_token") || !!localStorage.getItem("user_token") || !!localStorage.getItem("access_token"),
    staleTime: 30 * 1000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
    refetchInterval: 30 * 1000,
  });

  useEffect(() => {
    const channel = new BroadcastChannel('chops-updates');

    const handleChopsUpdate = () => {
      queryClient.invalidateQueries({ queryKey: ["userChops"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard", "stats"] });
      queryClient.invalidateQueries({ queryKey: ["currentUser"] });
    };

    channel.addEventListener('message', handleChopsUpdate);

    return () => {
      channel.removeEventListener('message', handleChopsUpdate);
      channel.close();
    };
  }, [queryClient]);

  return {
    ...query,
    invalidateChops: () => {
      queryClient.invalidateQueries({ queryKey: ["userChops"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard", "stats"] });
      queryClient.invalidateQueries({ queryKey: ["currentUser"] });

      const channel = new BroadcastChannel('chops-updates');
      channel.postMessage({ type: 'chops-updated' });
      channel.close();
    }
  };
};

export const updateChopsAfterAction = async (queryClient: any) => {
  queryClient.invalidateQueries({ queryKey: ["userChops"] });
  queryClient.invalidateQueries({ queryKey: ["dashboard", "stats"] });
  queryClient.invalidateQueries({ queryKey: ["currentUser"] });

  const channel = new BroadcastChannel('chops-updates');
  channel.postMessage({ type: 'chops-updated' });
  channel.close();
};
export const useBetaStatus = () => {
  return useQuery({
    queryKey: ["betaStatus"],
    queryFn: async () => {
      const token = localStorage.getItem("auth_token") || localStorage.getItem("access_token");
      if (!token) return null;

      const res = await axios.get("/api/stripe/beta/status", {
        headers: { Authorization: `Bearer ${token}` }
      });
      return res.data;
    },
    enabled: !!localStorage.getItem("auth_token") || !!localStorage.getItem("access_token"),
    staleTime: 60 * 1000,
    refetchInterval: 10000, // Refetch every 10 seconds for real-time countdown updates
  });
};
