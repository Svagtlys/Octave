import type { HTMLAttributes, ReactNode } from 'react';
import styles from './Badge.module.css';

export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'err';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  children: ReactNode;
}

/** Small status pill. Tone maps to the semantic palette (ok/warn/err/accent). */
export function Badge({ tone = 'neutral', className, children, ...rest }: BadgeProps) {
  const toneClass = {
    neutral: styles.neutral,
    accent: styles.accent,
    ok: styles.ok,
    warn: styles.warn,
    err: styles.err,
  }[tone];
  return (
    <span className={[styles.badge, toneClass, className].filter(Boolean).join(' ')} {...rest}>
      {children}
    </span>
  );
}
