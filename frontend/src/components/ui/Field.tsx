import type { ReactNode } from 'react';
import styles from './Field.module.css';

export interface FieldProps {
  /** Unique id of the control this field wraps; used for label association. */
  htmlFor: string;
  label: string;
  description?: string;
  /** Validation message. Presence puts the field in its error state. */
  error?: string;
  /** Slot for required markers or helper affordances next to the label. */
  hint?: ReactNode;
  children: ReactNode;
}

/**
 * Label + control + validation-message wrapper.
 *
 * Shaped to render MCP elicitation flat-form fields (string/number/boolean/
 * enum with per-field validation messages) without per-site markup.
 */
export function Field({ htmlFor, label, description, error, hint, children }: FieldProps) {
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={htmlFor}>
        {label}
        {hint}
      </label>
      {description && <p className={styles.description}>{description}</p>}
      {children}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
