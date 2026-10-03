import { Outlet } from '@tanstack/react-router';
import type { CSSProperties } from 'react';
import { colors } from '../../theme';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

/**
 * Root layout for every view: sidebar + top bar + content area.
 * Route views render inside <main> via <Outlet/>.
 */
export default function AppShell() {
  return (
    <div style={styles.shell}>
      <Sidebar />
      <div style={styles.mainColumn}>
        <TopBar />
        <main id="main-content" style={styles.content}>
          <Outlet />
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
