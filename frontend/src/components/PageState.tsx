/**
 * 載入／空／錯誤／重試 四種狀態的共用呈現。
 *
 * 每個資料頁面都經過這裡，確保狀態呈現一致，也避免每頁重複實作。
 */
import { Alert, Button, Empty, Spin } from 'antd';
import type { ReactNode } from 'react';

interface Props {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyText?: string;
  onRetry?: () => void;
  children: ReactNode;
}

export function PageState({ loading, error, empty, emptyText, onRetry, children }: Props) {
  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="載入失敗"
        description={error}
        action={
          onRetry ? (
            <Button size="small" danger onClick={onRetry}>
              重試
            </Button>
          ) : undefined
        }
      />
    );
  }

  if (empty) {
    return <Empty description={emptyText ?? '尚無資料'} />;
  }

  return <>{children}</>;
}
