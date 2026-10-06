/**
 * Auth state for the whole app (issue #122).
 *
 * Statuses:
 * - `loading`        initial probe in flight
 * - `setup_required` no user exists yet → SetupView owns the UI
 * - `anonymous`      users exist, nobody logged in → LoginView / redirect
 * - `authed`         cookie valid, `user` populated
 *
 * On mount: POST /auth/status → if setup_required stop there; otherwise
 * GET /auth/me to resolve the cookie. A 401 on any *other* API call
 * (session died mid-use) clears state via the client's unauthorized
 * handler.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  bootstrap as apiBootstrap,
  fetchAuthStatus,
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  setUnauthorizedHandler,
  type AuthUser,
} from '../api/client';

export type AuthStatus = 'loading' | 'anonymous' | 'authed' | 'setup_required';

export interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  bootstrap: (input: { username: string; password: string; display_name: string }) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { setup_required } = await fetchAuthStatus();
        if (cancelled) return;
        if (setup_required) {
          setStatus('setup_required');
          return;
        }
        const me = await fetchMe();
        if (cancelled) return;
        setUser(me);
        setStatus('authed');
      } catch {
        if (cancelled) return;
        // 401 (no/expired cookie) and unreachable backend both resolve to
        // anonymous; HealthStatus reports an actual outage in the shell.
        setStatus('anonymous');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onUnauthorized = useCallback(() => {
    setUser(null);
    setStatus('anonymous');
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(onUnauthorized);
    return () => setUnauthorizedHandler(null);
  }, [onUnauthorized]);

  const login = useCallback(async (username: string, password: string) => {
    const authed = await apiLogin(username, password);
    setUser(authed);
    setStatus('authed');
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
    setStatus('anonymous');
  }, []);

  const bootstrap = useCallback(
    async (input: { username: string; password: string; display_name: string }) => {
      const authed = await apiBootstrap(input);
      setUser(authed);
      setStatus('authed');
    },
    []
  );

  const value = useMemo(
    () => ({ user, status, login, logout, bootstrap }),
    [user, status, login, logout, bootstrap]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}
