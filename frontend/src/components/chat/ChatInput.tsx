import { useCallback, useRef, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { Button, Textarea } from '../ui';
import styles from './ChatInput.module.css';

/** ~6 visible rows before the textarea stops growing and scrolls. */
const MAX_GROW_PX = 132;

export interface ChatInputProps {
  /** Called with the trimmed text on send; the input clears. */
  onSend: (text: string) => void;
  /** Disables the textarea and Send button (socket not open). */
  disabled: boolean;
}

/**
 * Chat composer (issue #44): auto-growing textarea + Send button.
 * Enter sends, Shift+Enter inserts a newline. Purely presentational —
 * transport and transcript state live in SessionChatView.
 */
export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const send = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    setText('');
    const el = textareaRef.current;
    if (el) el.style.height = 'auto';
    onSend(trimmed);
  }, [text, disabled, onSend]);

  const handleChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    setText(event.target.value);
    // Auto-grow: reflow to content height, capped so the composer never
    // outgrows the transcript (scrolls beyond the cap).
    const el = event.currentTarget;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_GROW_PX)}px`;
  }, []);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    },
    [send]
  );

  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <Textarea
        ref={textareaRef}
        className={styles.textarea}
        rows={1}
        value={text}
        aria-label="Message input"
        placeholder="Write a message…"
        disabled={disabled}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
      <Button type="submit" disabled={disabled || text.trim().length === 0}>
        Send
      </Button>
    </form>
  );
}
