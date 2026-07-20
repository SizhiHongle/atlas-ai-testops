import {
  MutationCache,
  QueryCache,
  QueryClient
} from "@tanstack/react-query";

import { ApiProblemError } from "@/shared/api/problem";
import { observeClientError } from "@/shared/observability/client";

function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiProblemError && error.status < 500) return false;
  return failureCount < 2;
}

export function createAtlasQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        observeClientError(error, {
          source: "query",
          operation: String(query.queryKey[0] ?? "unknown")
        });
      }
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        observeClientError(error, {
          source: "mutation",
          operation: String(mutation.options.mutationKey?.[0] ?? "unknown")
        });
      }
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: 10 * 60_000,
        refetchOnWindowFocus: false,
        retry: shouldRetry
      },
      mutations: {
        retry: false
      }
    }
  });
}
