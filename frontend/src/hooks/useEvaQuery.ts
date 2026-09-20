import * as React from "react";
import { getEvaClient, type EvaClient } from "../api/client";

export type QueryState<T> =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "ready"; data: T };

/**
 * Minimal deterministic reader over the typed transport boundary.
 * Deliberately not a data-fetching framework — B02A needs readable
 * loading/empty/error states for four GET endpoints, nothing more.
 */
export function useEvaQuery<T>(
  key: string,
  read: (client: EvaClient) => Promise<T>,
  client: EvaClient = getEvaClient()
): QueryState<T> {
  const [state, setState] = React.useState<QueryState<T>>({ status: "loading" });

  React.useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    read(client).then(
      (data) => {
        if (!cancelled) setState({ status: "ready", data });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      }
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client]);

  return state;
}
