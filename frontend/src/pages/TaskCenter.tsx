/**
 * 任務中心：狀態、進度、耗時、取消、重試、回跳。
 *
 * 只呈現通用任務資訊；業務細節留在各自的業務頁面。
 */
import { useEffect } from 'react';
import { Button, Card, Progress, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { Link } from 'react-router-dom';
import { TasksService } from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  streaming: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'warning',
};

function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '-';
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function TaskCenter() {
  const { submitting, run } = useSubmit();

  const tasks = useAsync(() =>
    TasksService.listTasks({ page: 1, pageSize: 50 }).then((r) => unwrap<any>(r)),
  );

  // 有進行中的任務時自動輪詢；全部結束後停止，避免無謂請求。
  useEffect(() => {
    const hasActive = (tasks.data?.items ?? []).some((task: any) =>
      ['pending', 'running', 'streaming'].includes(task.status),
    );
    if (!hasActive) return undefined;

    const timer = window.setInterval(() => tasks.reload(), 3000);
    return () => window.clearInterval(timer);
  }, [tasks.data, tasks.reload]);

  const act = (label: string, action: () => Promise<unknown>) =>
    run(async () => {
      try {
        await action();
        message.success(`${label}成功`);
        tasks.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          任務中心
        </Typography.Title>
        <Button size="small" onClick={tasks.reload}>
          重新整理
        </Button>
      </Space>

      <Card size="small">
        <PageState
          loading={tasks.loading}
          error={tasks.error}
          empty={!tasks.data?.items?.length}
          emptyText="尚無任務"
          onRetry={tasks.reload}
        >
          <Table
            size="small"
            rowKey="id"
            scroll={{ x: 'max-content' }}
            pagination={false}
            dataSource={tasks.data?.items ?? []}
            columns={[
              { title: '類型', dataIndex: 'task_kind', width: 180 },
              {
                title: '狀態',
                dataIndex: 'status',
                width: 100,
                render: (status: string) => <Tag color={STATUS_COLOR[status] ?? 'default'}>{status}</Tag>,
              },
              {
                title: '進度',
                width: 160,
                render: (_: unknown, row: any) => (
                  <Tooltip title={row.progress_message}>
                    <Progress
                      percent={row.progress}
                      size="small"
                      status={row.status === 'failed' ? 'exception' : undefined}
                    />
                  </Tooltip>
                ),
              },
              { title: '耗時', width: 90, render: (_: unknown, row: any) => formatElapsed(row.elapsed_ms) },
              {
                title: '錯誤',
                dataIndex: 'error',
                ellipsis: true,
                render: (error: string) => (error ? <Typography.Text type="danger">{error}</Typography.Text> : '-'),
              },
              {
                title: '關聯',
                width: 120,
                render: (_: unknown, row: any) =>
                  row.shot_id ? (
                    <Link to={`/shots/${row.shot_id}`}>分鏡</Link>
                  ) : row.chapter_id ? (
                    <Link to={`/chapters/${row.chapter_id}`}>章節</Link>
                  ) : row.project_id ? (
                    <Link to={`/projects/${row.project_id}`}>專案</Link>
                  ) : (
                    '-'
                  ),
              },
              {
                title: '操作',
                width: 140,
                render: (_: unknown, row: any) => (
                  <Space size="small">
                    {row.is_cancellable && (
                      <Button
                        size="small"
                        loading={submitting}
                        onClick={() =>
                          act('取消請求', () =>
                            TasksService.cancelTask({ taskId: row.id, requestBody: { reason: '使用者取消' } }),
                          )
                        }
                      >
                        取消
                      </Button>
                    )}
                    {['failed', 'cancelled'].includes(row.status) && (
                      <Button
                        size="small"
                        type="link"
                        loading={submitting}
                        onClick={() => act('重試', () => TasksService.retryTask({ taskId: row.id }))}
                      >
                        重試
                      </Button>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        </PageState>
      </Card>
    </div>
  );
}
