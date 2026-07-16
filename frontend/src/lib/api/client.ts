export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new ApiError(response.status, errorBody || response.statusText);
  }

  const text = await response.text();
  if (!text) return {} as T;
  return JSON.parse(text) as T;
}

export async function get<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("PUT", path, body);
}

export async function deleteRequest<T>(path: string): Promise<T> {
  return request<T>("DELETE", path);
}

export interface HealthResponse {
  status: string;
}

export interface VersionResponse {
  version: string;
  env: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>("/api/health");
}

export async function fetchVersion(): Promise<VersionResponse> {
  return get<VersionResponse>("/api/version");
}
