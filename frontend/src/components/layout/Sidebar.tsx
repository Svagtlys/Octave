import { Link } from '@tanstack/react-router';
import type { CSSProperties } from 'react';
import { colors } from '../../theme';
import HealthStatus from '../HealthStatus';

const NAV_ITEMS = [
  { to: '/sessions', label: 'Sessions', icon: '☰' },
  { to: '/vault', label: 'Vault', icon: '▤' },
  { to: '/mcp', label: 'MCP', icon: '⛓' },
  { to: '/agents', label: 'Agents', icon: '◎' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
] as const;

/** Fixed-width navigation sidebar: brand, view links, health footer. */
export default function Sidebar() {
  return (
    <aside style={styles.sidebar}>
      <div style={styles.brand}>
        <span style={styles.brandMark}>◐</span>
        <span>Octave</span>
      </div>
      <nav style={styles.nav} aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            style={styles.navLink}
            activeProps={{
              style: styles.navLinkActive,
              'aria-current': 'page',
            }}
          >
            <span aria-hidden="true" style={styles.navIcon}>
              {item.icon}
            </span>
            {item.label}
          </Link>
        ))}
      </nav>
      <div style={styles.footer}>
        <HealthStatus />
      </div>
    </aside>
  );
}

const styles: Record<string, CSSProperties> = {
  sidebar: {
    width: 210,
    flexShrink: 0,
    background: colors.bgDeep,
    borderRight: `1px solid ${colors.border}`,
    display: 'flex',
    flexDirection: 'column',
    padding: '16px 10px',
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 9,
    padding: '4px 10px 18px',
    fontWeight: 700,
    fontSize: '1.05rem',
    letterSpacing: '0.02em',
  },
  brandMark: {
    width: 26,
    height: 26,
    borderRadius: 7,
    background: `linear-gradient(135deg, ${colors.accent}, #5a4fcf)`,
    display: 'grid',
    placeItems: 'center',
    fontSize: 15,
    color: '#fff',
    fontWeight: 800,
  },
  nav: { display: 'flex', flexDirection: 'column', gap: 2, flex: 1 },
  navLink: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '9px 12px',
    borderRadius: 7,
    color: colors.textDim,
    fontSize: '0.9rem',
    textDecoration: 'none',
    borderLeft: `3px solid transparent`,
  },
  navLinkActive: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '9px 12px',
    borderRadius: 7,
    color: colors.text,
    fontSize: '0.9rem',
    fontWeight: 600,
    textDecoration: 'none',
    background: colors.accentSoft,
    borderLeft: `3px solid ${colors.accent}`,
  },
  navIcon: { width: 16, textAlign: 'center', opacity: 0.85 },
  footer: {
    borderTop: `1px solid ${colors.border}`,
    padding: '12px 10px 2px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
};
