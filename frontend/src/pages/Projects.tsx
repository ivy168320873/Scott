/** 專案列表：建立、搜尋、進入工作區。 */
import { useState } from 'react';
import { Button, Card, Form, Input, List, Modal, Select, Space, Tag, Typography, message } from 'antd';
import { Link } from 'react-router-dom';
import { ProjectsService } from '../services/generated';
import { errorMessage, unwrap } from '../lib/api';
import { useAsync, useSubmit } from '../lib/useAsync';
import { PageState } from '../components/PageState';

export function Projects() {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const { submitting, run } = useSubmit();

  const projects = useAsync(
    () => ProjectsService.listProjects({ page: 1, pageSize: 50, search: search || undefined }).then((r) => unwrap<any>(r)),
    [search],
  );

  const create = async () => {
    const values = await form.validateFields();
    await run(async () => {
      try {
        await ProjectsService.createProject({ requestBody: values });
        message.success('專案已建立');
        setOpen(false);
        form.resetFields();
        projects.reload();
      } catch (error) {
        message.error(errorMessage(error));
      }
    });
  };

  return (
    <div>
      <Space style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          專案
        </Typography.Title>
        <Input.Search placeholder="搜尋專案" allowClear onSearch={setSearch} style={{ width: 220 }} />
        <Button type="primary" onClick={() => setOpen(true)}>
          新增專案
        </Button>
      </Space>

      <PageState
        loading={projects.loading}
        error={projects.error}
        empty={!projects.data?.items?.length}
        emptyText="尚無專案"
        onRetry={projects.reload}
      >
        <List
          grid={{ gutter: 16, xs: 1, sm: 2, lg: 3 }}
          dataSource={projects.data?.items ?? []}
          renderItem={(item: any) => (
            <List.Item>
              <Card title={<Link to={`/projects/${item.id}`}>{item.name}</Link>} size="small">
                <Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary">
                  {item.description || '（無簡介）'}
                </Typography.Paragraph>
                <Space wrap>
                  <Tag>{item.status}</Tag>
                  <Tag>{item.default_video_ratio}</Tag>
                  {item.genre && <Tag color="blue">{item.genre}</Tag>}
                </Space>
              </Card>
            </List.Item>
          )}
        />
      </PageState>

      <Modal title="新增專案" open={open} onOk={create} confirmLoading={submitting} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical" initialValues={{ visual_style: 'live_action', default_video_ratio: '9:16' }}>
          <Form.Item name="name" label="專案名稱" rules={[{ required: true, message: '請輸入名稱' }]}>
            <Input placeholder="例如：都市甜寵" />
          </Form.Item>
          <Form.Item name="description" label="簡介">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="genre" label="題材">
            <Input placeholder="都市／古裝／科幻" />
          </Form.Item>
          <Form.Item name="style_prompt" label="風格提示詞">
            <Input.TextArea rows={2} placeholder="cinematic, warm tone" />
          </Form.Item>
          <Form.Item name="visual_style" label="畫面表現">
            <Select
              options={[
                { value: 'live_action', label: '真人' },
                { value: 'anime', label: '動漫' },
                { value: 'mixed', label: '混合' },
              ]}
            />
          </Form.Item>
          <Form.Item name="default_video_ratio" label="影片比例">
            <Select options={[{ value: '9:16' }, { value: '16:9' }, { value: '1:1' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
