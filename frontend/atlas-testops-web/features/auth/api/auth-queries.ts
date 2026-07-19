"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import { login, logout, readSession } from "./auth-service";
import type { LoginCommand, SessionViewModel } from "../model/session";

export const authQueryKeys = {
  all: ["auth"] as const,
  session: ["auth", "session"] as const
};

export function useSessionQuery() {
  return useQuery({
    queryKey: authQueryKeys.session,
    queryFn: readSession,
    staleTime: 30_000,
    retry: false
  });
}

export function useLoginMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: LoginCommand) => login(command),
    onSuccess: (session) => {
      queryClient.setQueryData<SessionViewModel>(
        authQueryKeys.session,
        session
      );
    }
  });
}

export function useLogoutMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSettled: async () => {
      queryClient.setQueryData(authQueryKeys.session, null);
      await queryClient.cancelQueries({ queryKey: authQueryKeys.all });
    }
  });
}
