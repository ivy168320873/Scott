/**
 * 專案工作區：章節、資產、素材整合為 Tab。
 *
 * 刻意不為每種資產各開一個大型頁面 —— Tab 已足夠完成 MVP 流程。
 */
import { useState } from 'react';
import { Button, Card, Form, Input, List, Modal, Table, Tabs, Tag, Typography, message } from 'antd';
import { Link, useParams } from 'react-router-dom';
import {
  CharactersService,
  CostumesService,
  ProjectsService,
  PropsService,
  ScenesService,
} from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

const ASSET_TABS = [
  { key: 'characters', label: '角色', service: CharactersService, list: 'listCharacters', create: 'createCharacter' },
  { key: 'scenes', label: '場景', service: ScenesService, list: 'listScenes', create: 'createScene' },
  { key: 'props', label: '道具', service: PropsService, list: 'listProps', create: 'createProp' },
  { key: 'costumes', label: '服裝', service: CostumesService, list: 'listCostumes', create: 'createCostume' },
] as const;

function AssetTab({ projectId, tab }: { projectId: string; tab: (typeof ASSET_TABS)[number] }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { submitting, run } = useSubmit();

  const assets = useAsync<{ items: any[] }>(
    async () => {
      const response = await (tab.service as any)[tab.list]({ projectId, page: 1, pageSize: 100 });
      return unwrap<{ items: any[] }>(response);
    },
    [projectId, tab.key],
  );

  const create = async () => {
    const values = await form.validateFields();
    await run(async () => {
      try {
        await (tab.service as any)[tab.create]({ projectId, requestBody: values });
        message.success(`${tab.label}已建立`);
        setOpen(false);
        form.resetFields();
        assets.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });
  };

  return (
    <>
      <Button type="primary" size="small" onClick={() => setOpen(true)} style={{ marginBottom: 12 }}>
        新增{tab.label}
      </Button>

      <PageState
        loading={assets.loading}
        error={assets.error}
        empty={!assets.data?.items?.length}
        emptyText={`尚無${tab.label}；可先執行 AI 提取再確認候選`}
        onRetry={assets.reload}
      >
        <List
          size="small"
          dataSource={assets.data?.items ?? []}
          renderItem={(item: any) => (
            <List.Item>
              <List.Item.Meta
                title={item.name}
                description={item.appearance_prompt || item.description || '（尚未填寫外觀描述）'}
              />
            </List.Item>
          )}
        />
      </PageState>

      <Modal
        title={`新增${tab.label}`}
        open={open}
        onOk={create}
        confirmLoading={submitting}
        onCancel={() => setOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名稱" rules={[{ required: true, message: '請輸入名稱' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="appearance_prompt"
            label="外觀提示詞"
            tooltip="生成時會附加於每個鏡頭的提示詞，用來維持跨鏡頭一致性"
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function ProjectWorkspace() {
  const { projectId = '' } = useParams();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { submitting, run } = useSubmit();

  const project = useAsync(
    () => ProjectsService.getProject({ projectId }).then((r) => unwrap<any>(r)),
    [projectId],
  );
  const chapters = useAsync(
    () => ProjectsService.listChapters({ projectId, page: 1, pageSize: 100 }).then((r) => unwrap<any>(r)),
    [projectId],
  );

  const createChapter = async () => {
    const values = await form.validateFields();
    await run(async () => {
      try {
        await ProjectsService.createChapter({ projectId, requestBody: values });
        message.success('章節已建立');
        setOpen(false);
        form.resetFields();
        chapters.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });
  };

  return (
    <div>
      <Typography.Title level={3}>{project.data?.name ?? '專案'}</Typography.Title>

      <Tabs
        items={[
          {
            key: 'chapters',
            label: '章節',
            children: (
              <Card
                size="small"
                extra={
                  <Button type="primary" size="small" onClick={() => setOpen(true)}>
                    新增章節
                  </Button>
                }
              >
                <PageState
                  loading={chapters.loading}
                  error={chapters.error}
                  empty={!chapters.data?.items?.length}
                  emptyText="尚無章節"
                  onRetry={chapters.reload}
                >
                  <Table
                    size="small"
                    rowKey="id"
                    scroll={{ x: 'max-content' }}
                    pagination={false}
                    dataSource={chapters.data?.items ?? []}
                    columns={[
                      { title: '#', dataIndex: 'index', width: 60 },
                      {
                        title: '標題',
                        dataIndex: 'title',
                        render: (title: string, row: any) => <Link to={`/chapters/${row.id}`}>{title}</Link>,
                      },
                      { title: '分鏡數', dataIndex: 'shot_count', width: 90 },
                      {
                        title: '腳本',
                        dataIndex: 'has_script',
                        width: 90,
                        render: (has: boolean) => (has ? <Tag color="green">已輸入</Tag> : <Tag>未輸入</Tag>),
                      },
                      { title: '狀態', dataIndex: 'status', width: 100 },
                    ]}
                  />
                </PageState>
              </Card>
            ),
          },
          {
            key: 'assets',
            label: '素材資產',
            children: (
              <Tabs
                type="card"
                items={ASSET_TABS.map((tab) => ({
                  key: tab.key,
                  label: tab.label,
                  children: <AssetTab projectId={projectId} tab={tab} />,
                }))}
              />
            ),
          },
        ]}
      />

      <Modal title="新增章節" open={open} onOk={createChapter} confirmLoading={submitting} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="章節標題" rules={[{ required: true, message: '請輸入標題' }]}>
            <Input placeholder="第一集" />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
