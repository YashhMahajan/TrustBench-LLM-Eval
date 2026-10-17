"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Press Escape to close a drawer. This is a keyboard action, so it has no animation. */
export function EscClose({ href }: { href: string }) {
  const router = useRouter();
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && router.push(href, { scroll: false });
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [href, router]);
  return null;
}
