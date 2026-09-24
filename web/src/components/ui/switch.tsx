import * as React from "react";
import { cn } from "@/lib/utils";

export const Switch = ({ checked, onCheckedChange, disabled }: { checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    disabled={disabled}
    onClick={() => onCheckedChange(!checked)}
    className={cn(
      "relative inline-flex h-5.5 w-10 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
      checked ? "bg-primary" : "bg-slate-300",
      disabled && "opacity-50 pointer-events-none"
    )}
    style={{ height: 22 }}
  >
    <span className={cn(
      "pointer-events-none block rounded-full bg-white shadow transition-transform",
      checked ? "translate-x-[19px]" : "translate-x-[3px]"
    )} style={{ width: 16, height: 16, marginTop: 3 }} />
  </button>
);
