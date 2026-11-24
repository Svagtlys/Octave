import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageList } from '../../src/components/chat/MessageList';
import type { TranscriptMessage } from '../../src/lib/api/sessions';

function msg(overrides: Partial<TranscriptMessage> = {}): TranscriptMessage {
  return {
    seq: 1,
    kind: 'user_message',
    author: 'Alice',
    content: 'Hello there',
    timestamp: '2026-11-22T14:02:00Z',
    ...overrides,
  };
}

describe('MessageList', () => {
  it('renders author, content and role badge for user and agent messages', () => {
    render(
      <MessageList
        messages={[
          msg(),
          msg({
            seq: 2,
            kind: 'assistant_message',
            author: 'Octave',
            content: 'Hi Alice!',
          }),
        ]}
      />
    );

    expect(screen.getByText('Alice')).toBeDefined();
    expect(screen.getByText('Hello there')).toBeDefined();
    expect(screen.getByText('Octave')).toBeDefined();
    expect(screen.getByText('Hi Alice!')).toBeDefined();
    expect(screen.getByText('You')).toBeDefined();
    expect(screen.getByText('Agent')).toBeDefined();
  });

  it('renders a timestamp element with the machine-readable datetime', () => {
    render(<MessageList messages={[msg()]} />);
    const time = document.querySelector('time');
    expect(time).not.toBeNull();
    expect(time?.getAttribute('datetime')).toBe('2026-11-22T14:02:00Z');
  });

  it('renders messages in seq order even when the input is unordered', () => {
    render(
      <MessageList
        messages={[
          msg({ seq: 3, content: 'third' }),
          msg({ seq: 1, content: 'first' }),
          msg({ seq: 2, content: 'second' }),
        ]}
      />
    );
    const items = screen.getByRole('list').children;
    const texts = Array.from(items).map((li) => li.textContent ?? '');
    expect(texts[0]).toContain('first');
    expect(texts[1]).toContain('second');
    expect(texts[2]).toContain('third');
  });

  it('shows an empty-state message when there are no messages', () => {
    render(<MessageList messages={[]} />);
    expect(screen.getByRole('status').textContent).toContain('No messages yet');
  });

  it('degrades gracefully for null author and null content', () => {
    render(<MessageList messages={[msg({ author: null, content: null })]} />);
    expect(screen.getByText('Unknown')).toBeDefined();
    expect(screen.getByText('[unavailable message content]')).toBeDefined();
  });

  it('builds avatar initials from first and last name parts', () => {
    const { rerender } = render(<MessageList messages={[msg({ author: 'Alice Lee' })]} />);
    expect(screen.getByText('AL')).toBeDefined();
    rerender(<MessageList messages={[msg({ author: 'Bob' })]} />);
    expect(screen.getByText('BO')).toBeDefined();
  });

  it('treats unknown kinds as non-agent (neutral role rendering)', () => {
    render(<MessageList messages={[msg({ kind: 'system' })]} />);
    expect(screen.getByText('You')).toBeDefined();
    expect(screen.queryByText('Agent')).toBeNull();
  });

  it('renders committed thinking as a collapsed details block', () => {
    render(
      <MessageList
        messages={[
          msg({
            kind: 'assistant_message',
            author: 'Octave',
            content: 'answer',
            thinking: 'because reasons',
          }),
        ]}
      />
    );
    const details = screen.getByText('Thinking').closest('details');
    expect(details).toBeDefined();
    expect(details?.textContent).toContain('because reasons');
    expect(screen.getByText('answer')).toBeDefined();
  });

  it('omits the thinking block when thinking is absent', () => {
    render(<MessageList messages={[msg({ kind: 'assistant_message', content: 'plain' })]} />);
    expect(screen.queryByText('Thinking')).toBeNull();
  });
});
