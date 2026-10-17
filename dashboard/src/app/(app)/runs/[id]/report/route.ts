import { cookies } from "next/headers";
import { API_URL, KEY_COOKIE, forwarded } from "@/lib/api";

/** Hands the Markdown report of a run to the browser. The key stays on the server side. */
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const key = (await cookies()).get(KEY_COOKIE)?.value;
  if (!key) return new Response("Sign in first.", { status: 401 });
  const model = new URL(request.url).searchParams.get("model");
  const res = await fetch(`${API_URL}/v1/runs/${encodeURIComponent(id)}/report${model ? `?model=${encodeURIComponent(model)}` : ""}`, {
    headers: { Authorization: `Bearer ${key}`, ...(await forwarded()) },
    cache: "no-store",
  }).catch(() => null);
  if (!res) return new Response("The TrustBench server is not answering.", { status: 502 });
  return new Response(res.ok ? await res.text() : (await res.json().catch(() => ({ error: "The report failed." }))).error, {
    status: res.status,
    headers: res.ok
      ? { "Content-Type": "text/markdown; charset=utf-8", "Content-Disposition": `attachment; filename="trustbench-run-${id}.md"` }
      : { "Content-Type": "text/plain; charset=utf-8" },
  });
}
