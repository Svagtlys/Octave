import { Outlet } from '@tanstack/react-router';
import type { CSSProperties } from 'react';
import { colors } from '../../theme';

/**
 * Root layout for every view: sidebar + top bar + content area.
 * Route views render inside <main> via <Outlet/>.
 */
export default function AppShell() {
  return (
    <div style={styles.shell}>
      <main style={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  shell: {
    display: 'flex',
    minHeight: '100vh',
    background: colors.bg,
  },
  content: {
    flex: 1,
    minWidth: 0,
    overflow: 'auto',
    padding: 24,
  },
};
