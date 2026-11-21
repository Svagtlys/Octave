import * as RadixCheckbox from '@radix-ui/react-checkbox';
import type { ComponentProps } from 'react';
import styles from './Checkbox.module.css';

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 12.5l5 5L20 6.5"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export interface CheckboxProps extends Omit<ComponentProps<typeof RadixCheckbox.Root>, 'children'> {
  label?: string;
}

/** Boolean control (Radix behavior: space/enter toggle, indeterminate). */
export function Checkbox({
  label,
  checked,
  onCheckedChange,
  id,
  className,
  ...rest
}: CheckboxProps) {
  return (
    <label className={[styles.wrapper, className].filter(Boolean).join(' ')} htmlFor={id}>
      <RadixCheckbox.Root
        id={id}
        className={styles.root}
        checked={checked}
        onCheckedChange={onCheckedChange}
        {...rest}
      >
        <RadixCheckbox.Indicator className={styles.indicator}>
          <CheckIcon />
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {label && <span className={styles.label}>{label}</span>}
    </label>
  );
}
