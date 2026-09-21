export type Asset = { id: string; name: string; width: number; height: number; bytes: number; url: string };
export type Draft = { prompt: string; mode: 'count' | 'queue' | 'per-image'; count: number; references: string[]; common_references: string[]; ratio: string; custom_size: { widthCm: string; heightCm: string } | null; image_size: '1K' | '2K' | '4K'; derivative: boolean };
export type Task = { id: string; index: number; prompt: string; effective_prompt: string; status: string; stage: string; error: string; results: Asset[]; account_id?: string };
export type Batch = { id: string; created: number; request: Draft; display_model: string; tasks: Task[] };
export type Settings = { configured: boolean; proxy_configured: boolean; display_model: string; upstream_model: string; credential_storage: string };
let session = '';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function bootstrap(): Promise<Settings> {
  const response = await fetch('/api/bootstrap');
  if (!response.ok) throw new Error('无法连接本地服务');
  const result = await response.json();
  session = result.session;
  return result.settings;
}

export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const headers: Record<string, string> = { 'X-Studio-Session': session };
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method: method ?? (body ? 'POST' : 'GET'), headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  if (!response.ok) throw new ApiError(typeof result.detail === 'string' ? result.detail : '请求未完成，请检查工作台任务状态', response.status);
  return result;
}

export async function download(path: string, name: string) {
  const response = await fetch(path, { headers: { 'X-Studio-Session': session } });
  if (!response.ok) throw new Error('下载失败，请刷新页面后再试');
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const initialDraft: Draft = { prompt: '', mode: 'count', count: 1, references: [], common_references: [], ratio: 'Adaptive', custom_size: null, image_size: '2K', derivative: false };
