interface Props { values: number[]; labels?: string[]; height?: number; highlight?: number; format?: (v: number) => string; title: string }

/** Small bars over time. The last `highlight` bars use the accent colour. */
export function Sparkbars({ values, labels = [], height = 44, highlight = 0, format = String, title }: Props) {
  if (values.length === 0) return null;
  const max = Math.max(...values, 1e-9);
  return (
    <div role="img" aria-label={title} style={{ display: "flex", alignItems: "flex-end", gap: 3, height }}>
      {values.map((v, i) => (
        <span key={i} title={`${labels[i] ?? i + 1}: ${format(v)}`}
          style={{ flex: "1 1 0", maxWidth: 22, minWidth: 3, height: `${Math.max(3, (v / max) * 100)}%`, borderRadius: 2,
            background: i >= values.length - highlight ? "var(--accent)" : "var(--rule-strong)" }} />
      ))}
    </div>
  );
}
