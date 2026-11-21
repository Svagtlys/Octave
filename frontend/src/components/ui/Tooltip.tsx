import * as RadixTooltip from '@radix-ui/react-tooltip';
import type { ReactNode } from 'react';
import styles from './Tooltip.module.css';

export interface TooltipProps {
  content: ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
  /** Delay before showing, ms. Default matches Radix (0); 300–700 is calmer. */
  delayDuration?: number;
  children: ReactNode;
}

/**
 * Hover/focus tooltip (Radix Tooltip). The provider must be mounted once at
 * the app root (see <TooltipProvider> export); nested providers warn.
 */
export function Tooltip({ content, side = 'top', delayDuration = 400, children }: TooltipProps) {
  return (
    <RadixTooltip.Root delayDuration={delayDuration}>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content className={styles.content} side={side} sideOffset={6}>
          {content}
          <RadixTooltip.Arrow className={styles.arrow} />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}

/** Mount once at the app root, above every <Tooltip>. */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return <RadixTooltip.Provider delayDuration={400}>{children}</RadixTooltip.Provider>;
}
