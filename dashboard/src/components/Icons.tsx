import type { ReactNode } from "react";

const P = ({ children }: { children: ReactNode }) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);

export const I = {
  overview: <P><rect x="2" y="2" width="5" height="5" rx="1" /><rect x="9" y="2" width="5" height="5" rx="1" /><rect x="2" y="9" width="5" height="5" rx="1" /><rect x="9" y="9" width="5" height="5" rx="1" /></P>,
  runs: <P><path d="M2 12 5.5 7l3 3L14 3" /><path d="M2 14h12" /></P>,
  gate: <P><path d="M8 1.5 13.5 4v4c0 3-2.3 5.2-5.5 6.5C4.8 13.2 2.5 11 2.5 8V4L8 1.5Z" /><path d="m5.5 8 2 2 3-3.5" /></P>,
  data: <P><ellipse cx="8" cy="4" rx="5" ry="2" /><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4" /><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" /></P>,
  models: <P><circle cx="4" cy="11" r="1.6" /><circle cx="8.5" cy="5" r="1.6" /><circle cx="12.5" cy="9" r="1.6" /><path d="M2 14.5h12M2 14.5V2" /></P>,
  monitor: <P><path d="M1.5 8h3l2-4.5 3 9 2-4.5h3" /></P>,
  settings: <P><circle cx="8" cy="8" r="2" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" /></P>,
  sun: <P><circle cx="8" cy="8" r="2.6" /><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" /></P>,
  logout: <P><path d="M6 2.5H3.5v11H6M10 5l3 3-3 3M13 8H6" /></P>,
  menu: <P><path d="M2 4h12M2 8h12M2 12h12" /></P>,
};

/** The mark: a point with its interval. */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="var(--accent)" />
      <path d="M5 12h14M5 8.5v7M19 8.5v7" stroke="#04161a" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2.6" fill="#04161a" />
    </svg>
  );
}
