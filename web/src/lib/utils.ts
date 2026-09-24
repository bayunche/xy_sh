import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined) return "-";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(String(ts).replace(" ", "T"));
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function fmtDur(sec: number | null | undefined): string {
  if (!sec || sec < 0) return "-";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h} 时 ${m} 分` : `${m} 分`;
}
