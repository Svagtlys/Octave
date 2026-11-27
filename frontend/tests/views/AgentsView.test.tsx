import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AgentsView from '../../src/views/AgentsView';
import * as summariesApi from '../../src/lib/api/summaries';
import { ApiError } from '../../src/lib/api/client';

vi.mock('../../src/lib/api/summaries');
const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

const row = (
  overrides: Partial<summariesApi.SessionSummaryListItem> = {}
): summariesApi.SessionSummaryListItem => ({
  id: 's_1',
  title: 'Weather check',
  created_at: '2026-11-01T00:00:00Z',
  last_activity: '2026-11-24T12:00:00Z',
  last_message: 'hello',
  summary: {
    title: 'Weather',
    snippet: 'Sunny day.',
    model_name: 'llama',
    generated_at: '2026-11-24T13:00:00Z',
  },
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue({
    items: [row(), row({ id: 's_2', title: null, summary: null })],
    total: 2,
    limit: 20,
    offset: 0,
  });
  vi.mocked(summariesApi.fetchSessionSummaryDetail).mockResolvedValue({
    session_id: 's_1',
    summary: {
      title: 'Weather',
      content: 'Checked the weather: sunny.',
      model_name: 'llama',
      generated_at: '2026-11-24T13:00:00Z',
    },
  });
});

describe('AgentsView', () => {
  it('renders the list with snippet and no-summary states', async () => {
    render(<AgentsView />);
    expect(
      await screen.findByRole('button', { name: /Weather check/ })
    ).toBeDefined();
    expect(screen.getByText('Sunny day.')).toBeDefined();
    expect(screen.getByText('Untitled')).toBeDefined();
    expect(screen.getAllByText('No summary yet').length).toBeGreaterThan(0);
  });

  it('selecting a row fetches and shows the detail', async () => {
    render(<AgentsView />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Weather check/ })
    );
    expect(await screen.findByText('Checked the weather: sunny.')).toBeDefined();
    expect(summariesApi.fetchSessionSummaryDetail).toHaveBeenCalledWith('s_1');
  });

  it('refresh regenerates and patches the detail', async () => {
    vi.mocked(summariesApi.refreshSessionSummary).mockResolvedValue({
      session_id: 's_1',
      summary: {
        title: 'Weather v2',
        content: 'Regenerated recap.',
        model_name: 'llama',
        generated_at: '2026-11-26T00:00:00Z',
      },
    });
    render(<AgentsView />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Weather check/ })
    );
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    // Both the patched list snippet and the synced detail body show it.
    expect((await screen.findAllByText('Regenerated recap.')).length).toBeGreaterThan(1);
    expect(summariesApi.refreshSessionSummary).toHaveBeenCalledWith('s_1');
  });

  it('refresh failure surfaces inline', async () => {
    vi.mocked(summariesApi.refreshSessionSummary).mockRejectedValue(
      new ApiError(503, 'off')
    );
    render(<AgentsView />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Weather check/ })
    );
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('503');
  });

  it('open chat navigates to the session transcript', async () => {
    render(<AgentsView />);
    await userEvent.click(
      await screen.findByRole('button', { name: /Weather check/ })
    );
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('link', { name: /open chat/i }));
    expect(navigate).toHaveBeenCalledWith({
      to: '/sessions/$sessionId',
      params: { sessionId: 's_1' },
    });
  });

  it('empty list shows the empty state', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    render(<AgentsView />);
    expect(await screen.findByText('No conversations yet.')).toBeDefined();
  });
});
