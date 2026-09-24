import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => (
    <input ref={ref} className={cn(
      "flex h-9 w-full rounded-lg border border-border bg-white px-3 py-1 text-sm shadow-sm transition placeholder:text-mutedfg/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-50",
      className)} {...p} />
  )
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => (
    <textarea ref={ref} className={cn(
      "flex min-h-[72px] w-full rounded-lg border border-border bg-white px-3 py-2 text-sm shadow-sm transition placeholder:text-mutedfg/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
      className)} {...p} />
  )
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...p }, ref) => (
    <select ref={ref} className={cn(
      "flex h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
      className)} {...p} />
  )
);
Select.displayName = "Select";

export const Label = ({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) => (
  <label className={cn("text-xs font-medium text-mutedfg", className)} {...p} />
);
