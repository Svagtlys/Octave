import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolCallCard } from '../../src/components/chat/ToolCallCard';

describe('ToolCallCard', () => {
  it('shows name, streaming arguments and calling status', () => {
    render(<ToolCallCard name="mcp__fs__write" status="calling" arguments='{"path"' />);
    expect(screen.getByText('mcp__fs__write')).toBeDefined();
    expect(screen.getByText('{"path"')).toBeDefined();
    expect(screen.getByText('calling tool')).toBeDefined();
  });

  it('shows output and done status', () => {
    render(
      <ToolCallCard name="mcp__fs__read" status="done" arguments="{}" output="file contents" />
    );
    expect(screen.getByText('file contents')).toBeDefined();
    expect(screen.getByText('done')).toBeDefined();
  });

  it('shows error status', () => {
    render(<ToolCallCard name="mcp__fs__read" status="error" arguments="{}" output="boom" />);
    expect(screen.getByText('error')).toBeDefined();
  });
});
