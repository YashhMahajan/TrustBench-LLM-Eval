"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="panel" style={{ maxWidth: 560, padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 8 }}>This page did not load</h1>
      <p style={{ color: "var(--ink-2)", margin: "0 0 14px" }}>The server answered with an error. Check the terminal where <span className="mono">trustbench serve</span> runs, then try again.</p>
      <button className="btn" onClick={reset}>Try again</button>
    </div>
  );
}
