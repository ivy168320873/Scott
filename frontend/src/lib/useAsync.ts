/**
 * 非同步資料載入 hook。
 *
 * 統一提供 loading / error / retry 三種狀態，讓每個頁面不必各自實作，
 * 也保證所有頁面的載入行為一致。
 */
import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from './api';

export interface AsyncState<T> {
  data: T | undefined;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    loader()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, loading, error, reload };
}

/**
 * 送出動作的 hook，內建重複送出防護。
 *
 * 生成任務成本高，重複點擊會產生多筆任務並重複計費，
 * 因此送出中一律鎖住。
 */
export function useSubmit() {
  const [submitting, setSubmitting] = useState(false);

  const run = useCallback(
    async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
      if (submitting) return undefined;
      setSubmitting(true);
      try {
        return await action();
      } finally {
        setSubmitting(false);
      }
    },
    [submitting],
  );

  return { submitting, run };
}
