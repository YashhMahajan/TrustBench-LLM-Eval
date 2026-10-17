import Link from "next/link";

export default function NotFound() {
  return (
    <div className="panel" style={{ maxWidth: 560, padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 8 }}>We could not find that</h1>
      <p style={{ color: "var(--ink-2)", margin: "0 0 16px" }}>The run or page does not exist in this workspace. It may belong to another workspace, or it was deleted.</p>
      <Link href="/runs" className="btn">See all runs</Link>
    </div>
  );
}
