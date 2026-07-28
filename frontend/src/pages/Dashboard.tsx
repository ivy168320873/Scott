/** 總覽：專案數、進行中任務與快速入口。 */
import { Card, Col, List, Row, Statistic, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { ProjectsService, TasksService } from '../services/generated';
import { unwrap } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { PageState } from '../components/PageState';

export function Dashboard() {
  const projects = useAsync(() =>
    ProjectsService.listProjects({ page: 1, pageSize: 5 }).then((r) => unwrap<any>(r)),
  );
  const tasks = useAsync(() => TasksService.listActiveTasks().then((r) => unwrap<any[]>(r)));

  return (
    <div>
      <Typography.Title level={3}>總覽</Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="專案數" value={projects.data?.meta?.total ?? 0} loading={projects.loading} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="進行中任務" value={tasks.data?.length ?? 0} loading={tasks.loading} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="最近專案" extra={<Link to="/projects">全部</Link>}>
            <PageState
              loading={projects.loading}
              error={projects.error}
              empty={!projects.data?.items?.length}
              emptyText="尚無專案，先建立一個吧"
              onRetry={projects.reload}
            >
              <List
                dataSource={projects.data?.items ?? []}
                renderItem={(item: any) => (
                  <List.Item>
                    <Link to={`/projects/${item.id}`}>{item.name}</Link>
                    <Tag>{item.status}</Tag>
                  </List.Item>
                )}
              />
            </PageState>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="進行中任務" extra={<Link to="/tasks">任務中心</Link>}>
            <PageState
              loading={tasks.loading}
              error={tasks.error}
              empty={!tasks.data?.length}
              emptyText="目前沒有進行中的任務"
              onRetry={tasks.reload}
            >
              <List
                dataSource={tasks.data ?? []}
                renderItem={(item: any) => (
                  <List.Item>
                    <span>{item.task_kind}</span>
                    <Tag color="processing">{item.progress}%</Tag>
                  </List.Item>
                )}
              />
            </PageState>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
