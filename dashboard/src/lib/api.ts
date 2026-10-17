import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const API_URL = (process.env.TRUSTBENCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
export const KEY_COOKIE = "tb_key";

export class Unreachable extends Error {}

/** Call the TrustBench server with the key from the cookie. The key never reaches the browser. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const key = (await cookies()).get(KEY_COOKIE)?.value;
  if (!key) redirect("/login");
  let res: Response;
  try {
    res = await fetch(API_URL + path, {
      ...init,
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new Unreachable(`Cannot reach the TrustBench server at ${API_URL}.`);
  }
  if (res.status === 401) redirect("/login?expired=1");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `The server answered ${res.status}.`);
  }
  return res.json() as Promise<T>;
}

/** Like api(), but returns null when the server says the thing does not exist. */
export async function apiOrNull<T>(path: string): Promise<T | null> {
  try {
    return await api<T>(path);
  } catch (e) {
    if (e instanceof Unreachable) throw e;
    return null;
  }
}
