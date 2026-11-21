import { forwardRef } from 'react';
import type { TextareaHTMLAttributes } from 'react';
import styles from './Input.module.css';

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Marks the control visually invalid; pair with <Field error=...>. */
  invalid?: boolean;
}

/** Multi-line text input. Use inside <Field> for labels + validation. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ invalid = false, className, rows = 4, ...rest }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={[styles.input, styles.textarea, invalid && styles.invalid, className]
        .filter(Boolean)
        .join(' ')}
      {...rest}
    />
  )
);
Textarea.displayName = 'Textarea';
