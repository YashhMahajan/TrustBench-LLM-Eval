interface Props { values: number[]; labels?: string[]; height?: number; highlight?: number; format?: (v: number) => string; title: string }

/** Small bars over time. The last `highlight` bars are drawn in the accent colour. */
export function Sparkbars({ values, labels = [], height = 44, highlight = 0, format = String, title }: Props) {
  if (values.length === 0) return null;
  const max = Math.max(...values, 1e-9);
  const w = 6, gap = 3, width = values.length * (w + gap) - gap;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label={title}>
      <title>{title}</title>
      {values.map((v, i) => {
        const h = Math.max(1.5, (v / max) * (height - 2));
        const hot = i >= values.length - highlight;
        return (
          <rect key={i} x={i * (w + gap)} y={height - h} width={w} height={h} rx={1.5}
            fill={hot ? "var(--accent)" : "var(--rule-strong)"}>
            <title>{`${labels[i] ?? i + 1}: ${format(v)}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}
