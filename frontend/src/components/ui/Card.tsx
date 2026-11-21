import type { HTMLAttributes, ReactNode } from 'react';
import styles from './Card.module.css';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Optional header row (title + actions); renders above a divider. */
  header?: ReactNode;
  /** Optional footer row rendered above a divider. */
  footer?: ReactNode;
  /** Remove inner padding for flush content (tables, media). */
  flush?: boolean;
}

/** Bordered panel on the surface color. The default content container. */
export function Card({ header, footer, flush = false, className, children, ...rest }: CardProps) {
  return (
    <div className={[styles.card, className].filter(Boolean).join(' ')} {...rest}>
      {header && <div className={styles.header}>{header}</div>}
      <div className={flush ? styles.bodyFlush : styles.body}>{children}</div>
      {footer && <div className={styles.footer}>{footer}</div>}
    </div>
  );
}
