/**
 * Studio 主框架。
 *
 * 側邊選單在桌面固定顯示，行動裝置改為抽屜，達成基本響應式；
 * 不追求複雜動畫。
 */
import { useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { Button, Drawer, Grid, Layout as AntLayout, Menu, Typography } from 'antd';
import {
  AppstoreOutlined,
  DashboardOutlined,
  MenuOutlined,
  PictureOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';

const { Header, Sider, Content } = AntLayout;

const NAV = [
  { key: '/', icon: <DashboardOutlined />, label: <Link to="/">總覽</Link> },
  { key: '/projects', icon: <AppstoreOutlined />, label: <Link to="/projects">專案</Link> },
  { key: '/media', icon: <PictureOutlined />, label: <Link to="/media">素材庫</Link> },
  { key: '/tasks', icon: <ThunderboltOutlined />, label: <Link to="/tasks">任務中心</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">設定</Link> },
];

export function StudioLayout() {
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const selected = NAV.map((item) => item.key)
    .filter((key) => (key === '/' ? location.pathname === '/' : location.pathname.startsWith(key)))
    .slice(-1);

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={selected}
      items={NAV}
      onClick={() => setDrawerOpen(false)}
      style={{ borderInlineEnd: 'none' }}
    />
  );

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 12, paddingInline: 16 }}>
        {!screens.md && (
          <Button
            type="text"
            icon={<MenuOutlined style={{ color: '#fff' }} />}
            onClick={() => setDrawerOpen(true)}
            aria-label="開啟選單"
          />
        )}
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          Scott Studio
        </Typography.Title>
        <div style={{ flex: 1 }} />
        <a href="/" style={{ color: 'rgba(255,255,255,0.75)' }}>
          回股票系統
        </a>
      </Header>

      <AntLayout>
        {screens.md ? (
          <Sider width={200} theme="light">
            {menu}
          </Sider>
        ) : (
          <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} placement="left" width={220}>
            {menu}
          </Drawer>
        )}

        <Content style={{ padding: screens.md ? 24 : 12, overflowX: 'auto' }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
}
