/**
 * Shared UI primitives (issue #7).
 *
 * These components are the seed of Octave's future component catalog for
 * agent-generated UI (see GLOSSARY.md "Component Catalog" and ADR-0001).
 * The name→component registry layer is intentionally deferred to a future
 * generative-UI ticket.
 */
export { Button } from './Button';
export type { ButtonProps, ButtonVariant, ButtonSize } from './Button';
export { Input } from './Input';
export type { InputProps } from './Input';
export { Textarea } from './Textarea';
export type { TextareaProps } from './Textarea';
export { Checkbox } from './Checkbox';
export type { CheckboxProps } from './Checkbox';
export { Select } from './Select';
export type { SelectProps, SelectOption } from './Select';
export { Field } from './Field';
export type { FieldProps } from './Field';
export { Card } from './Card';
export type { CardProps } from './Card';
export { Badge } from './Badge';
export type { BadgeProps, BadgeTone } from './Badge';
export { Modal } from './Modal';
export type { ModalProps } from './Modal';
export { Tooltip, TooltipProvider } from './Tooltip';
export type { TooltipProps } from './Tooltip';
