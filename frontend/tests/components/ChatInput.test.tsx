import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatInput } from '../../src/components/chat/ChatInput';

describe('ChatInput', () => {
  it('renders a labelled textarea and a Send button', () => {
    render(<ChatInput onSend={() => {}} disabled={false} />);
    expect(screen.getByRole('textbox', { name: /message input/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /^send$/i })).toBeDefined();
  });

  it('Enter sends the trimmed text and clears the input', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByRole('textbox', { name: /message input/i });
    await userEvent.type(box, '  hello there  {Enter}');
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('hello there');
    expect((box as HTMLTextAreaElement).value).toBe('');
  });

  it('Shift+Enter does not send', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByRole('textbox', { name: /message input/i });
    await userEvent.type(box, 'multi{Shift>}{Enter}{/Shift}line');
    expect(onSend).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe('multi\nline');
  });

  it('empty or whitespace-only input is never sent', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    const box = screen.getByRole('textbox', { name: /message input/i });
    await userEvent.type(box, '   {Enter}');
    expect(onSend).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: /^send$/i }) as HTMLButtonElement).disabled).toBe(
      true
    );
  });

  it('clicking Send submits the text', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={false} />);
    await userEvent.type(screen.getByRole('textbox', { name: /message input/i }), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }));
    expect(onSend).toHaveBeenCalledWith('hi');
  });

  it('disabled blocks typing paths: button disabled, Enter suppressed', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} disabled={true} />);
    const box = screen.getByRole('textbox', { name: /message input/i });
    expect((box as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /^send$/i }) as HTMLButtonElement).disabled).toBe(
      true
    );
    await userEvent.type(box, 'suppressed{Enter}');
    expect(onSend).not.toHaveBeenCalled();
  });
});
