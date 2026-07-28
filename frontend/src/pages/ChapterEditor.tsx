/**
 * 章節編輯：腳本輸入 + AI 工作流 + 分鏡列表。
 *
 * 這是主流程的中樞：輸入腳本 → 提交分析 → 產生分鏡 → 提取候選。
 */
import { useState } from 'react';
import { Alert, Button, Card, Input, Space, Table, Tag, Typography, message } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { ProjectsService, ShotsService, WorkflowService } from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

export function ChapterEditor() {
  const { chapterId = '' } = useParams();
  const [draft, setDraft] = useState<string | null>(null);
  const { submitting, run } = useSubmit();

  const chapter = useAsync(
    () => ProjectsService.getChapter({ chapterId }).then((r) => unwrap<any>(r)),
    [chapterId],
  );
  const script = useAsync(
    () => ProjectsService.getScript({ chapterId }).then((r) => unwrap<any>(r)),
    [chapterId],
  );
  const shots = useAsync(
    () => ShotsService.listShots({ chapterId, page: 1, pageSize: 200 }).then((r) => unwrap<any>(r)),
    [chapterId],
  );

  const text = draft ?? script.data?.raw_text ?? '';

  const saveScript = () =>
    run(async () => {
      try {
        await ProjectsService.updateScript({ chapterId, requestBody: { raw_text: text } });
        message.success('腳本已儲存');
        setDraft(null);
        script.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });

  /** 提交 AI 任務並提示使用者到任務中心查看進度。 */
  const submitTask = (label: string, action: () => Promise<unknown>) =>
    run(async () => {
      try {
        await action();
        message.success(`${label}任務已提交，可在任務中心查看進度`);
      } catch (error) {
        message.error(errorMessage(error));
      }
    });

  return (
    <div>
      <Typography.Title level={3}>{chapter.data?.title ?? '章節'}</Typography.Title>

      <Card title="腳本" size="small" style={{ marginBottom: 16 }}>
        <PageState loading={script.loading} error={script.error} onRetry={script.reload}>
          <Input.TextArea
            rows={10}
            value={text}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="貼上或輸入劇本內容…"
          />
          <Space style={{ marginTop: 12, flexWrap: 'wrap' }}>
            <Button type="primary" loading={submitting} disabled={draft === null} onClick={saveScript}>
              儲存腳本
            </Button>
            <Button
              loading={submitting}
              disabled={!script.data?.raw_text}
              onClick={() =>
                submitTask('拆分鏡', () =>
                  WorkflowService.analyzeChapterScript({ chapterId, requestBody: { replace_existing: true } }),
                )
              }
            >
              AI 拆分鏡
            </Button>
            <Button
              loading={submitting}
              disabled={!shots.data?.items?.length}
              onClick={() =>
                submitTask('實體提取', () => WorkflowService.extractChapterEntities({ chapterId, requestBody: {} }))
              }
            >
              AI 提取素材
            </Button>
            <Button
              loading={submitting}
              disabled={!script.data?.raw_text}
              onClick={() =>
                submitTask('腳本精簡', () => WorkflowService.simplifyChapterScript({ chapterId, requestBody: {} }))
              }
            >
              精簡腳本
            </Button>
            <Button
              loading={submitting}
              disabled={!shots.data?.items?.length}
              onClick={() =>
                submitTask('一致性檢查', () =>
                  WorkflowService.checkChapterConsistency({ chapterId, requestBody: {} }),
                )
              }
            >
              一致性檢查
            </Button>
          </Space>

          {!script.data?.raw_text && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 12 }}
              message="請先輸入並儲存腳本，才能執行 AI 分析"
            />
          )}
        </PageState>
      </Card>

      <Card
        title="分鏡"
        size="small"
        extra={
          <Button size="small" onClick={shots.reload}>
            重新整理
          </Button>
        }
      >
        <PageState
          loading={shots.loading}
          error={shots.error}
          empty={!shots.data?.items?.length}
          emptyText="尚無分鏡；先執行「AI 拆分鏡」"
          onRetry={shots.reload}
        >
          <Table
            size="small"
            rowKey="id"
            scroll={{ x: 'max-content' }}
            pagination={false}
            dataSource={shots.data?.items ?? []}
            columns={[
              { title: '#', dataIndex: 'index', width: 60 },
              {
                title: '標題',
                dataIndex: 'title',
                render: (title: string, row: any) => <Link to={`/shots/${row.id}`}>{title || '(未命名)'}</Link>,
              },
              {
                title: '確認狀態',
                dataIndex: 'status',
                width: 110,
                render: (status: string) =>
                  status === 'ready' ? <Tag color="green">已確認</Tag> : <Tag color="orange">待確認</Tag>,
              },
              {
                title: '時長',
                width: 80,
                render: (_: unknown, row: any) => `${row.detail?.duration_seconds ?? '-'}s`,
              },
              {
                title: '影片',
                width: 90,
                render: (_: unknown, row: any) =>
                  row.generated_video_file_id ? <Tag color="blue">已生成</Tag> : <Tag>未生成</Tag>,
              },
            ]}
          />
        </PageState>
      </Card>
    </div>
  );
}
