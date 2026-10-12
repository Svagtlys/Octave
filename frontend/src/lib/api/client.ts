export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// 401 interceptor: auth state clears app-wide when the session dies
// mid-request (expiry, server-side logout). useAuth registers the hook;
// auth endpoints opt out via skipAuthRedirect (their 401s are form
// feedback, not session-death signals).
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts?: { skipAuthRedirect?: boolean }
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    method,
    // Session cookie rides along on every request (issue #122: the
    // backend is cookie-auth; omitting this breaks all authenticated calls).
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    if (response.status === 401 && !opts?.skipAuthRedirect) {
      onUnauthorized?.();
    }
    const errorBody = await response.text();
    throw new ApiError(response.status, errorBody || response.statusText);
  }

  const text = await response.text();
  if (!text) return {} as T;
  return JSON.parse(text) as T;
}

export async function get<T>(path: string): Promise<T> {
  return request<T>('GET', path);
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, body);
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PUT', path, body);
}

export async function patch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PATCH', path, body);
}

export async function deleteRequest<T>(path: string): Promise<T> {
  return request<T>('DELETE', path);
}

export interface HealthResponse {
  status: string;
}

export interface VersionResponse {
  version: string;
  env: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>('/api/health');
}

export async function fetchVersion(): Promise<VersionResponse> {
  return get<VersionResponse>('/api/version');
}

// --- auth (issue #122) ---

export interface AuthUser {
  id: string;
  username: string;
  display_name: string;
  role: 'owner' | 'member';
}

export interface AuthStatusResponse {
  setup_required: boolean;
}

export async function fetchAuthStatus(): Promise<AuthStatusResponse> {
  return post<AuthStatusResponse>('/api/auth/status');
}

export async function fetchMe(): Promise<AuthUser> {
  // 401 here means "anonymous", not "session died while authed" — the
  // mount-time probe must not trigger the interceptor.
  return request<AuthUser>('GET', '/api/auth/me', undefined, {
    skipAuthRedirect: true,
  });
}

export async function login(username: string, password: string): Promise<AuthUser> {
  // A wrong password is a 401 the LoginView renders inline — not a
  // session-death signal.
  return request<AuthUser>(
    'POST',
    '/api/auth/login',
    { username, password },
    { skipAuthRedirect: true }
  );
}

export async function bootstrap(input: {
  username: string;
  password: string;
  display_name: string;
}): Promise<AuthUser> {
  return post<AuthUser>('/api/auth/bootstrap', input);
}

export async function logout(): Promise<void> {
  await post('/api/auth/logout');
}
