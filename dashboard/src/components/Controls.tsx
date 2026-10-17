import Link from "next/link";
import type { ReactNode } from "react";

export function Tabs({ items, current }: { items: { id: string; label: ReactNode; href: string }[]; current: string }) {
  return (
    <div className="tabs" role="tablist">
      {items.map((t) => (
        <Link key={t.id} href={t.href} role="tab" aria-selected={t.id === current} className="tab" scroll={false}>{t.label}</Link>
      ))}
    </div>
  );
}

export function Segmented({ items, current }: { items: { id: string; label: string; href: string }[]; current: string }) {
  return (
    <div className="seg" role="group">
      {items.map((t) => (
        <Link key={t.id} href={t.href} className="seg__item" aria-current={t.id === current ? "true" : undefined} scroll={false}>{t.label}</Link>
      ))}
    </div>
  );
}

export function Bar({ value, tone = "accent" }: { value: number; tone?: "accent" | "fail" | "warn" }) {
  return <span className="hbar"><span className={`hbar__fill hbar__fill--${tone}`} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} /></span>;
}
