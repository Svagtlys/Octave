/**
 * useAuth state transitions (issue #122). The client module is mocked so
 * transitions are asserted independent of fetch behavior.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import type { ReactNode } from 'react';

const mocks = vi.hoisted(() => ({
  fetchAuthStatus: vi.fn(),
  fetchMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  bootstrap: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string
    ) {
      super(message);
    }
  },
  fetchAuthStatus: mocks.fetchAuthStatus,
  fetchMe: mocks.fetchMe,
  login: mocks.login,
  logout: mocks.logout,
  bootstrap: mocks.bootstrap,
  setUnauthorizedHandler: mocks.setUnauthorizedHandler,
}));

import { AuthProvider, useAuth } from './useAuth';

const alice = { id: 'u1', username: 'alice', display_name: 'Alice', role: 'owner' as const };

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useAuth', () => {
  it('resolves setup_required when no user exists', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: true });
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.status).toBe('loading');
    await waitFor(() => expect(result.current.status).toBe('setup_required'));
    // No /me probe when setup is pending.
    expect(mocks.fetchMe).not.toHaveBeenCalled();
  });

  it('resolves authed when the cookie is valid', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: false });
    mocks.fetchMe.mockResolvedValueOnce(alice);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('authed'));
    expect(result.current.user).toEqual(alice);
  });

  it('resolves anonymous when /me 401s', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: false });
    mocks.fetchMe.mockRejectedValueOnce(new Error('401'));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('anonymous'));
    expect(result.current.user).toBeNull();
  });

  it('login success transitions to authed', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: false });
    mocks.fetchMe.mockRejectedValueOnce(new Error('401'));
    mocks.login.mockResolvedValueOnce(alice);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('anonymous'));
    await act(async () => {
      await result.current.login('alice', 'pw');
    });
    expect(result.current.status).toBe('authed');
    expect(result.current.user).toEqual(alice);
  });

  it('logout transitions to anonymous', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: false });
    mocks.fetchMe.mockResolvedValueOnce(alice);
    mocks.logout.mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('authed'));
    await act(async () => {
      await result.current.logout();
    });
    expect(result.current.status).toBe('anonymous');
    expect(result.current.user).toBeNull();
  });

  it('bootstrap transitions to authed', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: true });
    mocks.bootstrap.mockResolvedValueOnce(alice);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('setup_required'));
    await act(async () => {
      await result.current.bootstrap({
        username: 'alice',
        password: 'pw',
        display_name: 'Alice',
      });
    });
    expect(result.current.status).toBe('authed');
  });

  it('registers an unauthorized handler that clears state', async () => {
    mocks.fetchAuthStatus.mockResolvedValueOnce({ setup_required: false });
    mocks.fetchMe.mockResolvedValueOnce(alice);
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('authed'));

    const handler = mocks.setUnauthorizedHandler.mock.calls.at(-1)?.[0];
    expect(typeof handler).toBe('function');
    act(() => handler());
    expect(result.current.status).toBe('anonymous');
    expect(result.current.user).toBeNull();
  });
});
