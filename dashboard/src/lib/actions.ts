import "server-only";
import { redirect, unstable_rethrow } from "next/navigation";
import { api } from "./api";

/** Run a server action. A problem returns to the page with a message in the address, never as a crash. */
export async function attempt(back: string, fn: () => Promise<unknown>, ok?: string): Promise<never> {
  const sep = back.includes("?") ? "&" : "?";
  try {
    await fn();
  } catch (e) {
    unstable_rethrow(e);
    redirect(`${back.split("#")[0]}${sep}error=${encodeURIComponent(e instanceof Error ? e.message : "The action failed.")}${back.includes("#") ? "#" + back.split("#")[1] : ""}`);
  }
  redirect(ok ? `${back.split("#")[0]}${sep}done=${encodeURIComponent(ok)}${back.includes("#") ? "#" + back.split("#")[1] : ""}` : back);
}

export const post = (path: string, body?: unknown) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });
export const put = (path: string, body: unknown) => api(path, { method: "PUT", body: JSON.stringify(body) });
