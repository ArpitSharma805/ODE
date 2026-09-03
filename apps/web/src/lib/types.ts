export interface AgentStatus {
  status?: "pending" | "running" | "completed" | "failed";
  detail?: string;
  duration?: number | null;
  mcp_calls?: { server: string; tool: string; success?: boolean }[];
  discovered_repos?: string[];
  expanded_queries?: string[];
  signal_clusters?: Record<string, number>;
  signal_counts?: Record<string, number>;
  used_fallback?: boolean;
}

export interface StreamEvent {
  type:
    | "created"
    | "status"
    | "update"
    | "opportunity"
    | "answer"
    | "final"
    | "error"
    | "heartbeat";
  investigation_id?: number;
  query?: string;
  status?: Record<string, AgentStatus>;
  agent?: string;
  message?: string;
  answer?: { answer?: string };
  opportunities?: unknown[];
  signals?: unknown[];
  discovered_repos?: string[];
  synthesis?: {
    themes?: { name?: string; summary?: string }[];
    insights?: { statement?: string }[];
    problems?: { statement?: string }[];
    narrative?: string;
  } | null;
}

export interface InvestigationSummary {
  investigation_id: number;
  query: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  completed_at?: string | null;
}

export interface Investigation extends InvestigationSummary {
  final_state?: StreamEvent | null;
  agent_states?: Record<string, AgentStatus>;
  trace_log?: StreamEvent[];
  error?: string | null;
}
