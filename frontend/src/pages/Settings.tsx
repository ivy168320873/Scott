/**
 * 設定：供應商、模型、提示詞模板整合為 Tab。
 *
 * 安全提醒：API 金鑰**不**在這裡輸入 —— 只填寫環境變數名稱，
 * 實際金鑰由部署環境提供。介面只顯示「是否就緒」。
 */
import { useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  message,
  Typography,
} from 'antd';
import { ModelsService, PromptTemplatesService, ProvidersService } from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

function ProvidersTab() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { submitting, run } = useSubmit();

  const providers = useAsync(() =>
    ProvidersService.listProviders({ page: 1, pageSize: 50 }).then((r) => unwrap<any>(r)),
  );

  const create = async () => {
    const values = await form.validateFields();
    await run(async () => {
      try {
        await ProvidersService.createProvider({ requestBody: values });
        message.success('供應商已建立');
        setOpen(false);
        form.resetFields();
        providers.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });
  };

  const test = (providerId: string) =>
    run(async () => {
      try {
        const result = unwrap<any>(await ProvidersService.testProvider({ providerId }));
        result.ok ? message.success(result.detail) : message.warning(result.detail);
      } catch (error) {
        message.error(errorMessage(error));
      }
    });

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="金鑰只透過環境變數提供"
        description="此處只填寫環境變數「名稱」（例如 ANTHROPIC_API_KEY），系統不會儲存或顯示金鑰內容。"
      />

      <Button type="primary" size="small" onClick={() => setOpen(true)} style={{ marginBottom: 12 }}>
        新增供應商
      </Button>

      <PageState
        loading={providers.loading}
        error={providers.error}
        empty={!providers.data?.items?.length}
        emptyText="尚無供應商"
        onRetry={providers.reload}
      >
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          scroll={{ x: 'max-content' }}
          dataSource={providers.data?.items ?? []}
          columns={[
            { title: '名稱', dataIndex: 'name' },
            { title: '類型', dataIndex: 'provider_type', width: 150 },
            { title: '金鑰變數', dataIndex: 'api_key_env', width: 190 },
            {
              title: '金鑰狀態',
              dataIndex: 'api_key_configured',
              width: 110,
              render: (ok: boolean) => (ok ? <Tag color="green">已設定</Tag> : <Tag color="red">未設定</Tag>),
            },
            {
              title: '啟用',
              dataIndex: 'enabled',
              width: 80,
              render: (enabled: boolean) => (enabled ? <Tag color="blue">是</Tag> : <Tag>否</Tag>),
            },
            {
              title: '操作',
              width: 90,
              render: (_: unknown, row: any) => (
                <Button size="small" loading={submitting} onClick={() => test(row.id)}>
                  測試
                </Button>
              ),
            },
          ]}
        />
      </PageState>

      <Modal title="新增供應商" open={open} onOk={create} confirmLoading={submitting} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ provider_type: 'anthropic', enabled: true }}>
          <Form.Item name="name" label="名稱" rules={[{ required: true, message: '請輸入名稱' }]}>
            <Input placeholder="Anthropic" />
          </Form.Item>
          <Form.Item name="provider_type" label="類型">
            <Select
              options={[
                { value: 'anthropic', label: 'Anthropic' },
                { value: 'openai', label: 'OpenAI' },
                { value: 'gemini', label: 'Gemini' },
                { value: 'openai_compatible', label: 'OpenAI 相容' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="api_key_env"
            label="金鑰環境變數名稱"
            tooltip="只填名稱，不要填入金鑰本身"
            rules={[{ required: true, message: '請輸入環境變數名稱' }]}
          >
            <Input placeholder="ANTHROPIC_API_KEY" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL">
            <Input placeholder="https://api.anthropic.com" />
          </Form.Item>
          <Form.Item name="timeout_seconds" label="逾時（秒）">
            <InputNumber min={1} max={3600} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function ModelsTab() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { submitting, run } = useSubmit();

  const models = useAsync(() => ModelsService.listModels({ page: 1, pageSize: 50 }).then((r) => unwrap<any>(r)));
  const providers = useAsync(() =>
    ProvidersService.listProviders({ page: 1, pageSize: 50 }).then((r) => unwrap<any>(r)),
  );

  const create = async () => {
    const values = await form.validateFields();
    await run(async () => {
      try {
        await ModelsService.createModel({ requestBody: values });
        message.success('模型已建立');
        setOpen(false);
        form.resetFields();
        models.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });
  };

  return (
    <>
      <Button type="primary" size="small" onClick={() => setOpen(true)} style={{ marginBottom: 12 }}>
        新增模型
      </Button>

      <PageState
        loading={models.loading}
        error={models.error}
        empty={!models.data?.items?.length}
        emptyText="尚無模型；請先新增供應商再設定模型"
        onRetry={models.reload}
      >
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          scroll={{ x: 'max-content' }}
          dataSource={models.data?.items ?? []}
          columns={[
            { title: '名稱', dataIndex: 'name' },
            { title: '模型識別碼', dataIndex: 'model_id' },
            { title: '類別', dataIndex: 'category', width: 90 },
            { title: '供應商', dataIndex: 'provider_name', width: 140 },
            {
              title: '啟用',
              dataIndex: 'enabled',
              width: 80,
              render: (enabled: boolean) => (enabled ? <Tag color="blue">是</Tag> : <Tag>否</Tag>),
            },
          ]}
        />
      </PageState>

      <Modal title="新增模型" open={open} onOk={create} confirmLoading={submitting} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ category: 'text', enabled: true }}>
          <Form.Item name="provider_id" label="供應商" rules={[{ required: true, message: '請選擇供應商' }]}>
            <Select
              options={(providers.data?.items ?? []).map((item: any) => ({ value: item.id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item name="name" label="顯示名稱" rules={[{ required: true, message: '請輸入名稱' }]}>
            <Input placeholder="Opus 5" />
          </Form.Item>
          <Form.Item name="model_id" label="模型識別碼" rules={[{ required: true, message: '請輸入識別碼' }]}>
            <Input placeholder="claude-opus-5" />
          </Form.Item>
          <Form.Item name="category" label="類別">
            <Select
              options={[
                { value: 'text', label: '文字' },
                { value: 'image', label: '圖片' },
                { value: 'video', label: '影片' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function PromptsTab() {
  const templates = useAsync(() =>
    PromptTemplatesService.listPromptTemplates({ page: 1, pageSize: 50 }).then((r) => unwrap<any>(r)),
  );

  return (
    <PageState
      loading={templates.loading}
      error={templates.error}
      empty={!templates.data?.items?.length}
      emptyText="尚無提示詞模板；系統會使用內建預設提示詞"
      onRetry={templates.reload}
    >
      <Table
        size="small"
        rowKey="id"
        pagination={false}
        scroll={{ x: 'max-content' }}
        dataSource={templates.data?.items ?? []}
        columns={[
          { title: '名稱', dataIndex: 'name' },
          { title: '類別', dataIndex: 'category', width: 170 },
          {
            title: '預設',
            dataIndex: 'is_default',
            width: 80,
            render: (isDefault: boolean) => (isDefault ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
          },
          { title: '內容', dataIndex: 'content', ellipsis: true },
        ]}
      />
    </PageState>
  );
}

export function Settings() {
  return (
    <div>
      <Typography.Title level={3}>設定</Typography.Title>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Tabs
          items={[
            { key: 'providers', label: '供應商', children: <ProvidersTab /> },
            { key: 'models', label: '模型', children: <ModelsTab /> },
            { key: 'prompts', label: '提示詞模板', children: <PromptsTab /> },
          ]}
        />
      </Space>
    </div>
  );
}
