import * as React from "react";
import { cn } from "@/lib/utils";

const tones = {
  default: "bg-muted text-mutedfg",
  success: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
  warn: "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
  danger: "bg-red-50 text-red-700 ring-1 ring-red-200",
  info: "bg-sky-50 text-sky-700 ring-1 ring-sky-200",
  purple: "bg-violet-50 text-violet-700 ring-1 ring-violet-200",
};

export const Badge = ({ className, tone = "default", ...p }: React.HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof tones }) => (
  <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", tones[tone], className)} {...p} />
);
