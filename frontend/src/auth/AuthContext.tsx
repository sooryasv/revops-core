import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";

import { api, ApiError, call, callVoid, type Schemas } from "../api/client";
import { can as canRole, type Permission } from "./permissions";

export type Me = Schemas["MeResponse"];

interface AuthValue {
  me: Me | null;
  loading: boolean;
  can: (permission: Permission) => boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);
const ME_KEY = ["me"] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await call(api.GET("/api/v1/auth/me"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });

  // Any 401 from the API means the session is gone: clear everything and let the router send the person to /login.
  useEffect(() => {
    const onUnauthorized = () => {
      queryClient.setQueryData(ME_KEY, null);
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    };
    window.addEventListener("revops:unauthorized", onUnauthorized);
    return () => window.removeEventListener("revops:unauthorized", onUnauthorized);
  }, [queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const me = await call(api.POST("/api/v1/auth/login", { body: { email, password } }));
      queryClient.setQueryData(ME_KEY, me);
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await callVoid(api.POST("/api/v1/auth/logout", { body: { everywhere: false } }));
    } finally {
      queryClient.clear();
      queryClient.setQueryData(ME_KEY, null);
    }
  }, [queryClient]);

  const me = query.data ?? null;
  const value = useMemo<AuthValue>(
    () => ({ me, loading: query.isPending, can: (p) => canRole(me?.role, p), login, logout }),
    [me, query.isPending, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
