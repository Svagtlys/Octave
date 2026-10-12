import { Outlet } from '@tanstack/react-router';
import type { CSSProperties, ReactNode } from 'react';
import { colors } from '../../theme';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

/**
 * Root layout for every view: sidebar + top bar + content area.
 * Route views render inside <main> via <Outlet/>; callers that replace the
 * outlet (the not-found route) can pass explicit children instead.
 */
export default function AppShell({ children }: { children?: ReactNode }) {
  return (
    <div style={styles.shell}>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <Sidebar />
      <div style={styles.mainColumn}>
        <TopBar />
        <main id="main-content" style={styles.content}>
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  shell: {
    display: 'flex',
    minHeight: '100vh',
    background: colors.bg,
  },
  mainColumn: {
    flex: 1,
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
  },
  content: {
    flex: 1,
    minWidth: 0,
    overflow: 'auto',
    padding: 24,
  },
};
