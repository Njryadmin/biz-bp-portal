// apps/web/lib/ai-models.ts
//
// Client-side helpers for the runtime-toggleable LLM provider registry
// (admin-only). Mirrors the pattern of apps/web/lib/auth.ts so the
// admin UI gets the same cookie-based fetch wrapper with no surprises.

export type AIProviderName =
  | "openai"
  | "deepseek"
  | "ollama"
  | "mock"
  | "anthropic"
  | "custom";

export const AI_PROVIDER_OPTIONS: { value: AIProviderName; label: string }[] = [
  { value: "mock", label: "Mock (built-in, no network)" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic (via OpenAI-compatible proxy)" },
  { value: "ollama", label: "Ollama (local)" },
  { value: "custom", label: "Custom (OpenAI-compatible)" },
];

export interface AIModelItem {
  id: number;
  name: string;
  provider: string;
  model_name: string;
  base_url: string | null;
  api_key_set: boolean;
  api_key_is_env_ref: boolean;
  enabled: boolean;
  is_default: boolean;
  is_active: boolean;
  last_tested_at: string | null;
  last_test_status: string | null;
  last_test_latency_ms: number | null;
  last_test_response: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIModelListResponse {
  count: number;
  models: AIModelItem[];
}

export interface CreateAIModelPayload {
  name: string;
  provider: AIProviderName;
  model_name: string;
  base_url?: string;
  api_key?: string;
  enabled?: boolean;
  is_default?: boolean;
}

export interface UpdateAIModelPayload {
  name?: string;
  provider?: AIProviderName;
  model_name?: string;
  base_url?: string;
  api_key?: string;
  enabled?: boolean;
  is_default?: boolean;
  is_active?: boolean;
}

export interface TestAIModelPayload {
  prompt?: string;
  max_tokens?: number;
}

export interface TestAIModelResponse {
  ok: boolean;
  status: string;
  latency_ms: number;
  sample_response: string;
  error: string | null;
}

async function readJson(res: Response) {
  if (res.status === 204) return null;
  return res.json().catch(() => null);
}

async function apiJson<T>(input: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(input, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = (await readJson(res)) as { detail?: string } | null;
    const message =
      body?.detail || `${init.method ?? "GET"} ${input} failed (HTTP ${res.status})`;
    const err = new Error(message) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return ((await readJson(res)) as T) ?? ({} as T);
}

export async function listAIModels(): Promise<AIModelListResponse> {
  return apiJson<AIModelListResponse>("/api/ai-models", { method: "GET" });
}

export async function createAIModel(
  payload: CreateAIModelPayload,
): Promise<AIModelItem> {
  return apiJson<AIModelItem>("/api/ai-models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAIModel(
  modelId: number,
  payload: UpdateAIModelPayload,
): Promise<AIModelItem> {
  return apiJson<AIModelItem>(`/api/ai-models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteAIModel(modelId: number): Promise<AIModelItem> {
  return apiJson<AIModelItem>(`/api/ai-models/${modelId}`, { method: "DELETE" });
}

export async function testAIModel(
  modelId: number,
  payload: TestAIModelPayload = {},
): Promise<TestAIModelResponse> {
  return apiJson<TestAIModelResponse>(`/api/ai-models/${modelId}/test`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function setDefaultAIModel(modelId: number): Promise<AIModelItem> {
  return apiJson<AIModelItem>(`/api/ai-models/${modelId}/set-default`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Pure helpers (no fetch) — used by the page component
// ---------------------------------------------------------------------------

export function providerLabel(provider: string): string {
  const opt = AI_PROVIDER_OPTIONS.find((o) => o.value === provider);
  return opt ? opt.label : provider;
}

export function providerColor(provider: string): string {
  switch (provider) {
    case "mock":
      return "default";
    case "deepseek":
      return "geekblue";
    case "openai":
      return "green";
    case "anthropic":
      return "purple";
    case "ollama":
      return "orange";
    case "custom":
      return "magenta";
    default:
      return "default";
  }
}
