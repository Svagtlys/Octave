import { forwardRef } from 'react';
import type { InputHTMLAttributes } from 'react';
import styles from './Input.module.css';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Marks the control visually invalid; pair with <Field error=...>. */
  invalid?: boolean;
}

/** Single-line text/number input. Use inside <Field> for labels + validation. */
export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ invalid = false, className, ...rest }, ref) => (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={[styles.input, invalid && styles.invalid, className].filter(Boolean).join(' ')}
      {...rest}
    />
  )
);
Input.displayName = 'Input';
