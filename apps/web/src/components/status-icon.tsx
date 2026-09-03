"use client";

import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

export function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-5 w-5 text-emerald-600" />;
    case "running":
      return <Loader2 className="h-5 w-5 animate-spin text-foreground" />;
    case "failed":
      return <XCircle className="h-5 w-5 text-destructive" />;
    default:
      return <Circle className="h-5 w-5 text-muted-foreground" />;
  }
}
