import { forwardRef } from 'react';
import type { ButtonHTMLAttributes } from 'react';
import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

/** Primary action button. One primary per view; secondary/ghost for the rest. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className, ...rest }, ref) => {
    const variantClass = {
      primary: styles.primary,
      secondary: styles.secondary,
      ghost: styles.ghost,
      danger: styles.danger,
    }[variant];
    const sizeClass = size === 'sm' ? styles.sm : styles.md;
    return (
      <button
        ref={ref}
        className={[styles.base, variantClass, sizeClass, className].filter(Boolean).join(' ')}
        {...rest}
      />
    );
  },
);
Button.displayName = 'Button';
