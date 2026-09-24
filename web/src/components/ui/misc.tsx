import * as React from "react";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

export const Spinner = ({ className, size }: { className?: string; size?: number }) => (
  <span style={size ? { width: size, height: size } : undefined} className={cn("inline-block h-4 w-4 animate-spin rounded-full border-2 border-primary/30 border-t-primary", className)} />
);

export const Empty = ({ children = "暂无数据" }: { children?: React.ReactNode }) => (
  <div className="py-10 text-center text-sm text-mutedfg">{children}</div>
);

export const Dialog = ({ open, onClose, title, children, wide }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; wide?: boolean;
}) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <div className={cn("max-h-[85vh] w-full overflow-auto rounded-xl bg-white shadow-xl", wide ? "max-w-2xl" : "max-w-md")} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button className="rounded-md p-1 text-mutedfg hover:bg-muted" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
};

export const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
  <div className="flex flex-col gap-1.5">
    <span className="text-xs font-medium text-mutedfg">{label}</span>
    {children}
    {hint && <span className="text-[11px] text-mutedfg/80">{hint}</span>}
  </div>
);

export const ErrorText = ({ children }: { children?: React.ReactNode }) =>
  children ? <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{children}</p> : null;
