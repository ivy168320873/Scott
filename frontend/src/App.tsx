/** Studio 路由。全部掛在 /studio 之下，不佔用根路徑。 */
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhTW from 'antd/locale/zh_TW';
import { StudioLayout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Projects } from './pages/Projects';
import { ProjectWorkspace } from './pages/ProjectWorkspace';
import { ChapterEditor } from './pages/ChapterEditor';
import { ShotStudio } from './pages/ShotStudio';
import { MediaLibrary } from './pages/MediaLibrary';
import { TaskCenter } from './pages/TaskCenter';
import { Settings } from './pages/Settings';

export default function App() {
  return (
    <ConfigProvider locale={zhTW}>
      <BrowserRouter basename="/studio">
        <Routes>
          <Route element={<StudioLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="projects" element={<Projects />} />
            <Route path="projects/:projectId" element={<ProjectWorkspace />} />
            <Route path="chapters/:chapterId" element={<ChapterEditor />} />
            <Route path="shots/:shotId" element={<ShotStudio />} />
            <Route path="media" element={<MediaLibrary />} />
            <Route path="tasks" element={<TaskCenter />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
