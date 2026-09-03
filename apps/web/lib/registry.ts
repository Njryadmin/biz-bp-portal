// apps/web/lib/registry.ts
import type { BusinessLine } from "@fin-bp/types";

export interface RegistryResponse {
  lines: BusinessLine[];
  version?: string;
}

const FALLBACK: RegistryResponse = { lines: [], version: "0.0.0" };

/**
 * Client-side fetch of the registry. Use this from "use client" components.
 * In server components, fetch /api/registry/lines directly.
 */
export async function fetchRegistryClient(): Promise<RegistryResponse> {
  try {
    const res = await fetch("/api/registry", { cache: "no-store" });
    if (!res.ok) return FALLBACK;
    return (await res.json()) as RegistryResponse;
  } catch {
    return FALLBACK;
  }
}
