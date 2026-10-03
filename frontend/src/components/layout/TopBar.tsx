import { useRouterState } from '@tanstack/react-router';
import type { CSSProperties } from 'react';
import { colors } from '../../theme';

/**
 * Top bar: view title (from the matched route's staticData.title) plus a
 * reserved command-palette slot that ships hidden/disabled until a future
 * issue enables it.
 */
export default function TopBar() {
  const title = useRouterState({
    select: (s) => {
      let found: string | undefined;
      for (const match of s.matches) {
        const staticTitle = match.staticData?.title;
        if (typeof staticTitle === 'string') found = staticTitle;
      }
      return found;
    },
  });

  return (
    <header style={styles.topbar}>
      <h1 style={styles.title}>{title ?? 'Octave'}</h1>
      <div style={styles.spacer} />
      <div aria-hidden="true" style={styles.paletteSlot}>
        <input
          data-testid="command-palette-input"
          disabled
          placeholder="Search or run a command… (Ctrl+K)"
          style={styles.paletteInput}
        />
      </div>
    </header>
  );
}

const styles: Record<string, CSSProperties> = {
  topbar: {
    height: 52,
    flexShrink: 0,
    borderBottom: `1px solid ${colors.border}`,
    display: 'flex',
    alignItems: 'center',
    padding: '0 20px',
    gap: 14,
    background: colors.panel,
  },
  title: { fontSize: '1rem', fontWeight: 600 },
  spacer: { flex: 1 },
  paletteSlot: { display: 'none' },
  paletteInput: {
    fontSize: '0.8rem',
    color: colors.textDim,
    background: colors.bgDeep,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    padding: '6px 12px',
    width: 200,
  },
};
