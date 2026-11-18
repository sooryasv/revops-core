import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** Filters, sort and page live in the URL so views can be bookmarked and shared (03 section 10). */
export function useUrlState<K extends string>(keys: readonly K[], pageSize = 20) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""])) as Record<K, string>;
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const set = useCallback(
    (patch: Partial<Record<K | "offset", string>>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch) as [string, string | undefined][]) {
            if (v === undefined || v === "") next.delete(k);
            else next.set(k, v);
          }
          if (!("offset" in patch)) next.delete("offset"); // any filter change returns to page 1
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  return { values, offset, limit: pageSize, set };
}
