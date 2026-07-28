/**
 * 分鏡工作室：候選確認（準備）與生成（執行）。
 *
 * 依職責分成兩個 Tab：
 * - 準備：確認 AI 提取的資產與對白候選，把分鏡推進到 ready
 * - 生成：提示詞、關鍵幀、圖片與影片生成
 */
import { Alert, Button, Card, Descriptions, List, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { useParams } from 'react-router-dom';
import { ShotsService, WorkflowService } from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

export function ShotStudio() {
  const { shotId = '' } = useParams();
  const { submitting, run } = useSubmit();

  const shot = useAsync(() => ShotsService.getShot({ shotId }).then((r) => unwrap<any>(r)), [shotId]);
  const readiness = useAsync(
    () => ShotsService.getShotReadiness({ shotId }).then((r) => unwrap<any>(r)),
    [shotId],
  );
  const candidates = useAsync(
    () => ShotsService.listShotCandidates({ shotId }).then((r) => unwrap<any[]>(r)),
    [shotId],
  );
  const dialogueCandidates = useAsync(
    () => ShotsService.listShotDialogueCandidates({ shotId }).then((r) => unwrap<any[]>(r)),
    [shotId],
  );
  const frames = useAsync(
    () => ShotsService.listShotFrames({ shotId }).then((r) => unwrap<any[]>(r)),
    [shotId],
  );

  const reloadAll = () => {
    shot.reload();
    readiness.reload();
    candidates.reload();
    dialogueCandidates.reload();
    frames.reload();
  };

  const act = (label: string, action: () => Promise<unknown>) =>
    run(async () => {
      try {
        await action();
        message.success(`${label}完成`);
        reloadAll();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });

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
      <Typography.Title level={3}>{shot.data?.title || '分鏡'}</Typography.Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2, md: 4 }}>
          <Descriptions.Item label="確認狀態">
            {shot.data?.status === 'ready' ? <Tag color="green">已確認</Tag> : <Tag color="orange">待確認</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="影片準備度">
            {readiness.data?.video_ready ? <Tag color="green">就緒</Tag> : <Tag color="orange">未就緒</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="待確認資產">{readiness.data?.pending_candidate_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="時長">{shot.data?.detail?.duration_seconds ?? '-'}s</Descriptions.Item>
        </Descriptions>

        {readiness.data?.blocking_reasons?.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="尚未具備影片生成條件"
            description={readiness.data.blocking_reasons.join('、')}
          />
        )}
      </Card>

      <Tabs
        items={[
          {
            key: 'prep',
            label: '準備（確認候選）',
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <Card title="資產候選" size="small">
                  <PageState
                    loading={candidates.loading}
                    error={candidates.error}
                    empty={!candidates.data?.length}
                    emptyText="尚無候選；先在章節頁執行「AI 提取素材」"
                    onRetry={candidates.reload}
                  >
                    <Table
                      size="small"
                      rowKey="id"
                      pagination={false}
                      scroll={{ x: 'max-content' }}
                      dataSource={candidates.data ?? []}
                      columns={[
                        { title: '類型', dataIndex: 'candidate_type', width: 90 },
                        { title: '名稱', dataIndex: 'name' },
                        { title: '描述', dataIndex: 'description', ellipsis: true },
                        {
                          title: '狀態',
                          dataIndex: 'status',
                          width: 90,
                          render: (status: string) =>
                            status === 'pending' ? <Tag color="orange">待確認</Tag> : <Tag>{status}</Tag>,
                        },
                        {
                          title: '操作',
                          width: 100,
                          render: (_: unknown, row: any) =>
                            row.status === 'pending' ? (
                              <Button
                                size="small"
                                loading={submitting}
                                onClick={() =>
                                  act('忽略', () =>
                                    ShotsService.resolveShotCandidate({
                                      candidateId: row.id,
                                      requestBody: { action: 'ignore' },
                                    }),
                                  )
                                }
                              >
                                忽略
                              </Button>
                            ) : null,
                        },
                      ]}
                    />
                  </PageState>
                </Card>

                <Card title="對白候選" size="small">
                  <PageState
                    loading={dialogueCandidates.loading}
                    error={dialogueCandidates.error}
                    empty={!dialogueCandidates.data?.length}
                    emptyText="尚無對白候選"
                    onRetry={dialogueCandidates.reload}
                  >
                    <List
                      size="small"
                      dataSource={dialogueCandidates.data ?? []}
                      renderItem={(item: any) => (
                        <List.Item
                          actions={
                            item.status === 'pending'
                              ? [
                                  <Button
                                    key="accept"
                                    size="small"
                                    type="link"
                                    loading={submitting}
                                    onClick={() =>
                                      act('接受', () =>
                                        ShotsService.resolveShotDialogueCandidate({
                                          candidateId: item.id,
                                          requestBody: { action: 'accept' },
                                        }),
                                      )
                                    }
                                  >
                                    接受
                                  </Button>,
                                  <Button
                                    key="ignore"
                                    size="small"
                                    type="link"
                                    loading={submitting}
                                    onClick={() =>
                                      act('忽略', () =>
                                        ShotsService.resolveShotDialogueCandidate({
                                          candidateId: item.id,
                                          requestBody: { action: 'ignore' },
                                        }),
                                      )
                                    }
                                  >
                                    忽略
                                  </Button>,
                                ]
                              : undefined
                          }
                        >
                          <List.Item.Meta title={item.speaker_name || '（旁白）'} description={item.content} />
                          <Tag>{item.status}</Tag>
                        </List.Item>
                      )}
                    />
                  </PageState>
                </Card>

                <Button
                  loading={submitting}
                  onClick={() => act('狀態重算', () => ShotsService.recomputeShotStatus({ shotId }))}
                >
                  重算確認狀態
                </Button>
              </Space>
            ),
          },
          {
            key: 'generate',
            label: '生成',
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <Card title="生成動作" size="small">
                  <Space wrap>
                    <Button
                      loading={submitting}
                      onClick={() =>
                        submitTask('提示詞生成', () =>
                          WorkflowService.generateShotPrompts({ shotId, requestBody: {} }),
                        )
                      }
                    >
                      AI 產生提示詞
                    </Button>
                    <Button
                      type="primary"
                      loading={submitting}
                      onClick={() =>
                        submitTask('圖片生成', () =>
                          WorkflowService.generateShotImages({ shotId, requestBody: { frame_types: ['first'] } }),
                        )
                      }
                    >
                      生成首幀圖片
                    </Button>
                    <Button
                      type="primary"
                      danger
                      loading={submitting}
                      onClick={() =>
                        submitTask('影片生成', () =>
                          WorkflowService.generateShotVideo({ shotId, requestBody: {} }),
                        )
                      }
                    >
                      生成影片
                    </Button>
                  </Space>
                </Card>

                <Card title="關鍵幀" size="small">
                  <PageState
                    loading={frames.loading}
                    error={frames.error}
                    empty={!frames.data?.length}
                    emptyText="尚無關鍵幀；先執行「AI 產生提示詞」"
                    onRetry={frames.reload}
                  >
                    <List
                      size="small"
                      dataSource={frames.data ?? []}
                      renderItem={(item: any) => (
                        <List.Item>
                          <List.Item.Meta
                            title={<Tag>{item.frame_type}</Tag>}
                            description={item.prompt || '（尚無提示詞）'}
                          />
                          {item.file_id ? <Tag color="green">已生成</Tag> : <Tag>未生成</Tag>}
                        </List.Item>
                      )}
                    />
                  </PageState>
                </Card>

                <Card title="影片提示詞" size="small">
                  <Typography.Paragraph type="secondary">
                    {shot.data?.detail?.video_prompt || '（尚未產生）'}
                  </Typography.Paragraph>
                </Card>
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}
