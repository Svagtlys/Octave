import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { fetchHealth } from '../lib/api/client';
import { colors } from '../theme';

type HealthState = 'checking' | 'healthy' | 'unreachable';

const PILL: Record<HealthState, { label: string; fg: string; bg: string; border: string }> = {
  checking: { label: 'Checking…', fg: colors.warn, bg: colors.warnSoft, border: colors.warn },
  healthy: { label: 'Healthy', fg: colors.ok, bg: colors.okSoft, border: colors.ok },
  unreachable: { label: 'Unreachable', fg: colors.err, bg: colors.errSoft, border: colors.err },
};

/**
 * Compact backend-health pill for the sidebar footer.
 * Supersedes the old HealthCheck widget; routes through fetchHealth()
 * (the apiFetch client) instead of raw fetch.
 */
export default function HealthStatus() {
  const [state, setState] = useState<HealthState>('checking');

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then(() => {
        if (!cancelled) setState('healthy');
      })
      .catch(() => {
        if (!cancelled) setState('unreachable');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pill = PILL[state];
  return (
    <span
      data-testid="health-pill"
      style={{ ...styles.pill, color: pill.fg, background: pill.bg, borderColor: pill.border }}
    >
      <span aria-hidden="true" style={{ ...styles.dot, background: pill.fg }} />
      {pill.label}
    </span>
  );
}

const styles: Record<string, CSSProperties> = {
  pill: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 7,
    fontSize: '0.74rem',
    fontWeight: 600,
    padding: '5px 11px',
    borderRadius: 999,
    border: '1px solid',
    whiteSpace: 'nowrap',
  },
  dot: { width: 7, height: 7, borderRadius: '50%' },
};
