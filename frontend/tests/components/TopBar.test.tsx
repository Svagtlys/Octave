import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { routeTree } from '../../src/router';

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

describe('TopBar', () => {
  it('shows the staticData title of the current route as the page heading', async () => {
    renderAt('/mcp');
    expect(await screen.findByRole('heading', { level: 1, name: 'MCP Servers' })).toBeDefined();
  });

  it('shows the Settings title on /settings', async () => {
    renderAt('/settings');
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeDefined();
  });

  it('keeps the command-palette slot hidden and inert', async () => {
    renderAt('/sessions');
    await screen.findByRole('heading', { level: 1, name: 'Sessions' });
    const input = document.querySelector<HTMLInputElement>(
      '[data-testid=command-palette-input]'
    );
    expect(input).not.toBeNull();
    expect(input?.disabled).toBe(true);
    expect(input?.closest('[aria-hidden=true]')).not.toBeNull();
  });
});
