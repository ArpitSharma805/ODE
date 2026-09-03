"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  BookOpen,
  Cpu,
  GitBranch,
  Layers,
  Layout,
  Loader2,
  MessageSquare,
  Search,
  Target,
  TrendingUp,
  Zap,
  Database,
  Sparkles,
  Compass,
} from "lucide-react";

import { StatusIcon } from "@/components/status-icon";
import { Investigation, InvestigationSummary } from "@/lib/types";

interface AgentState {
  status: "pending" | "running" | "completed" | "failed";
  detail?: string;
  duration?: number | null;
  mcp_calls?: { server: string; tool: string; success?: boolean }[];
  signal_counts?: Record<string, number>;
  expanded_queries?: string[];
}

interface PipelineNode {
  name: string;
  status: AgentState["status"];
  detail?: string;
  duration?: number | null;
}

interface SourceInfo {
  name: string;
  source_type: string;
  status: string;
  calls?: number;
}

interface Counts {
  signals: number;
  trends: number;
  opportunities: number;
  investigations: number;
  themes: number;
}

/**
 * Metric sources for the Live Architecture dashboard:
 *
 * signals: COUNT(*) FROM signals (aggregate across all investigations)
 * trends: COUNT(*) FROM trends (aggregate across all investigations)
 * opportunities: COUNT(*) FROM opportunities (aggregate across all investigations)
 * investigations: COUNT(*) FROM investigations (aggregate across all investigations)
 * themes: pipeline_artifacts["themes"] from latest investigation only
 *
 * Note: Themes are stored in pipeline_artifacts during the report agent execution,
 * not in a separate database table, so they only reflect the latest investigation.
 * All other metrics are aggregate counts across all investigations in the database.
 */

interface ArchitectureData {
  pipeline: PipelineNode[];
  sources: SourceInfo[];
  counts: Counts;
  themes: string[];
  latest: Investigation | null;
}

const PIPELINE_ICONS: Record<string, React.ReactNode> = {
  Query: <Search className="h-4 w-4" />,
  Intent: <MessageSquare className="h-4 w-4" />,
  "Research Planner": <Layout className="h-4 w-4" />,
  "Signal Analyst": <Zap className="h-4 w-4" />,
  "Trend Analyst": <TrendingUp className="h-4 w-4" />,
  "Opportunity Analyst": <Target className="h-4 w-4" />,
  "Report Agent": <Activity className="h-4 w-4" />,
};

function sourceIcon(source: SourceInfo) {
  const text = `${source.name} ${source.source_type}`.toLowerCase();
  if (text.includes("github")) return <GitBranch className="h-4 w-4" />;
  if (text.includes("tavily")) return <Search className="h-4 w-4" />;
  if (text.includes("playwright")) return <Cpu className="h-4 w-4" />;
  if (text.includes("context7") || text.includes("context")) return <Layers className="h-4 w-4" />;
  return <Activity className="h-4 w-4" />;
}

function StatusBadge({ status }: { status: string }) {
  const classes = {
    completed: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20",
    running: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20",
    failed: "bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20",
    pending: "bg-neutral-200/50 text-neutral-500 dark:bg-neutral-800",
  };
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize ${classes[status as keyof typeof classes] || classes.pending}`}
    >
      {status}
    </span>
  );
}

function formatDuration(seconds?: number | null) {
  if (seconds === undefined || seconds === null) return null;
  return `${seconds.toFixed(2)}s`;
}

function PipelineFlow({ pipeline }: { pipeline: PipelineNode[] }) {
  return (
    <div className="rounded-xl border border-neutral-300/90 bg-white p-6 dark:border-neutral-700/80 dark:bg-[#14161A] shadow-sm space-y-5">
      <div className="flex items-center justify-between border-b border-neutral-200/60 pb-3 dark:border-neutral-800/60">
        <div>
          <h3 className="text-sm font-semibold text-neutral-900 dark:text-white">Active Agent Pipeline</h3>
          <p className="text-xs text-neutral-500">Live LangGraph node execution flow</p>
        </div>
        <span className="px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
          Graph: StateGraph(ODEState)
        </span>
      </div>

      <div className="space-y-3">
        {pipeline.map((node, idx) => (
          <div key={node.name} className="flex items-center justify-between p-3 rounded-lg bg-neutral-50/70 dark:bg-[#181B20] border border-neutral-200/60 dark:border-neutral-800/60">
            <div className="flex items-center gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-neutral-200/70 dark:bg-neutral-800 text-xs font-mono font-bold text-neutral-700 dark:text-neutral-300">
                {idx + 1}
              </div>
              <div>
                <div className="text-xs font-semibold text-neutral-900 dark:text-white">{node.name}</div>
                <div className="text-[11px] text-neutral-500 dark:text-neutral-400">{node.detail || node.status}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {node.duration !== undefined && node.duration !== null && (
                <span className="text-xs font-mono text-neutral-400">{formatDuration(node.duration)}</span>
              )}
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                node.status === 'completed'
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
                  : node.status === 'running'
                  ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20 animate-pulse'
                  : 'bg-neutral-200/50 text-neutral-500 dark:bg-neutral-800'
              }`}>
                {node.status}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SourceIntegrations({ sources }: { sources: SourceInfo[] }) {
  return (
    <div className="rounded-xl border border-neutral-300/90 bg-white p-6 dark:border-neutral-700/80 dark:bg-[#14161A] shadow-sm space-y-4">
      <div className="flex items-center justify-between border-b border-neutral-200/60 pb-3 dark:border-neutral-800/60">
        <h3 className="text-sm font-semibold text-neutral-900 dark:text-white">Active Source Integrations</h3>
        <span className="text-xs text-neutral-400">Live MCP connections</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
        {sources.map((source, idx) => (
          <div key={idx} className="p-3.5 rounded-lg border border-neutral-200/60 bg-neutral-50/60 dark:border-neutral-800 dark:bg-[#181B20] space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-neutral-900 dark:text-white">{source.name}</span>
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
            </div>
            <div className="text-[11px] text-neutral-500 capitalize">
              {source.source_type.replace(/_/g, " ")}
            </div>
            <div className="text-[10px] font-mono text-neutral-400 pt-1">
              {source.calls ?? 0} calls
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ThemeList({ themes }: { themes: string[] }) {
  if (themes.length === 0) return null;
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium uppercase tracking-widest text-neutral-500">
        Top Themes
      </h3>
      <div className="flex flex-wrap gap-2">
        {themes.slice(0, 10).map((theme) => (
          <span key={theme} className="px-2.5 py-1 rounded-md text-xs font-medium bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            {theme}
          </span>
        ))}
      </div>
    </div>
  );
}

function CountCards({ counts, sourcesCount }: { counts: Counts; sourcesCount: number }) {
  const items = [
    { label: "Total Signals", value: counts.signals, icon: Activity, sub: "Live cross-source" },
    { label: "Active Sources", value: sourcesCount, icon: Database, sub: "MCP connected" },
    { label: "Opportunities", value: counts.opportunities, icon: Sparkles, sub: "Scored theses" },
    { label: "Investigations", value: counts.investigations, icon: Compass, sub: "Completed runs" },
    { label: "Themes Mapped", value: counts.themes, icon: Layers, sub: "Active patterns" },
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3.5 mb-8">
      {items.map((item, idx) => (
        <div key={idx} className="rounded-xl border border-neutral-300/90 bg-white p-4 dark:border-neutral-700/80 dark:bg-[#14161A] shadow-sm">
          <div className="flex items-center justify-between text-neutral-500 dark:text-neutral-400 mb-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider">{item.label}</span>
            <item.icon className="h-3.5 w-3.5" />
          </div>
          <div className="font-mono text-2xl font-bold text-neutral-900 dark:text-white">
            {item.value ?? 0}
          </div>
          <div className="text-[10px] text-neutral-400 mt-0.5">{item.sub}</div>
        </div>
      ))}
    </div>
  );
}

function RecentInvestigations({
  investigations,
}: {
  investigations: InvestigationSummary[];
}) {
  const router = useRouter();

  if (investigations.length === 0) return null;

  const getTimeAgo = (date: string) => {
    const now = new Date();
    const then = new Date(date);
    const diffMs = now.getTime() - then.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
  };

  return (
    <div className="rounded-xl border border-neutral-300/90 bg-white p-5 dark:border-neutral-700/80 dark:bg-[#14161A] space-y-4">
      <div className="flex items-center justify-between border-b border-neutral-200/60 pb-3 dark:border-neutral-800/60">
        <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          Recent Investigations
        </h3>
        <span className="text-[11px] text-neutral-400">Last 10 queries</span>
      </div>

      <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1 custom-scrollbar">
        {investigations.slice(0, 10).map((inv) => (
          <div
            key={inv.investigation_id}
            className="flex items-center justify-between p-3 rounded-lg border border-neutral-200/60 bg-white dark:border-neutral-800 dark:bg-[#181B20] hover:border-neutral-300 dark:hover:border-neutral-700 transition-colors"
          >
            <div className="min-w-0 flex-1 pr-3">
              <div className="text-xs font-medium text-neutral-900 dark:text-neutral-100 truncate">{inv.query}</div>
              <div className="text-[10px] text-neutral-400 mt-0.5">{getTimeAgo(inv.started_at)}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                {inv.status}
              </span>
              <button
                onClick={() => router.push(`/?investigation=${inv.investigation_id}`)}
                className="px-2.5 py-1 rounded-md text-xs font-medium bg-neutral-100 hover:bg-neutral-200 dark:bg-neutral-800 dark:hover:bg-neutral-700 text-neutral-700 dark:text-neutral-200 transition-colors"
              >
                Open
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ArchitecturePage() {
  const [data, setData] = useState<ArchitectureData | null>(null);
  const [investigations, setInvestigations] = useState<InvestigationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [archRes, invRes] = await Promise.all([
          fetch("/api/architecture"),
          fetch("/api/investigations?limit=20"),
        ]);
        if (!archRes.ok || !invRes.ok) {
          throw new Error("Failed to load architecture data");
        }
        const arch = (await archRes.json()) as ArchitectureData;
        const invs = (await invRes.json()) as InvestigationSummary[];
        if (!cancelled) {
          setData(arch);
          setInvestigations(invs);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    load();
    const interval = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const latest = data?.latest;
  const active = latest?.status === "running";

  return (
    <main className="relative min-h-screen overflow-x-hidden bg-transparent text-foreground">

      <div className="mx-auto max-w-7xl px-6 pt-6 pb-16 space-y-6">
        <header className="header-scrim p-8 border border-neutral-300/90 dark:border-neutral-700/80 shadow-sm">
          <div className="mb-4 inline-flex items-center rounded-full border border-border bg-card px-4 py-1.5 text-xs font-medium uppercase tracking-widest text-muted-foreground">
            Live Architecture
          </div>
          <h1 className="text-4xl font-semibold tracking-tight text-foreground md:text-5xl">
            How ODE works
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
            Real-time view of the agent pipeline, active source integrations, and
            the latest investigation.
          </p>
        </header>

        {error && (
          <div className="rounded-2xl border border-destructive/20 bg-destructive/10 p-4 text-destructive">
            {error}
          </div>
        )}

        {data && <CountCards counts={data.counts} sourcesCount={data.sources?.length || 0} />}

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left Column: Live Agent Pipeline DAG & Trace (7 cols) */}
          <div className="lg:col-span-7 space-y-6">
            {data ? (
              <PipelineFlow pipeline={data.pipeline} />
            ) : (
              <div className="flex h-64 items-center justify-center rounded-xl border border-neutral-300/90 bg-white dark:border-neutral-700/80 dark:bg-[#14161A]">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            )}

            {data ? <SourceIntegrations sources={data.sources} /> : null}
          </div>

          {/* Right Column: Latest & Recent Investigations (5 cols) */}
          <div className="lg:col-span-5 space-y-6">
            <div className="rounded-xl border border-neutral-300/90 bg-white p-6 dark:border-neutral-700/80 dark:bg-[#14161A] shadow-sm">
              <div className="flex items-center justify-between border-b border-neutral-200/60 pb-3 dark:border-neutral-800/60">
                <div>
                  <h3 className="text-sm font-semibold text-neutral-900 dark:text-white">Latest Investigation</h3>
                  <p className="text-xs text-neutral-500">
                    {active ? "A query is running right now." : "Most recent completed run."}
                  </p>
                </div>
              </div>
              <div className="pt-4">
                {latest ? (
                  <div className="space-y-4">
                    <div>
                      <div className="font-medium text-neutral-900 dark:text-white">{latest.query}</div>
                      <div className="text-sm text-neutral-500">
                        Started {new Date(latest.started_at).toLocaleString()}
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <StatusBadge status={latest.status} />
                      {active && (
                        <Loader2 className="h-4 w-4 animate-spin text-foreground" />
                      )}
                    </div>
                    <Link
                      href={`/?investigation=${latest.investigation_id}`}
                      className="mt-2 inline-flex w-full items-center justify-center rounded-lg border border-neutral-300/90 bg-white px-3 py-2 text-sm font-medium text-neutral-900 transition-colors hover:bg-neutral-50 dark:border-neutral-700/80 dark:bg-[#14161A] dark:text-white dark:hover:bg-[#181B20]"
                    >
                      {active ? "Watch live result" : "View report"}
                    </Link>
                    <ThemeList themes={data?.themes || []} />
                  </div>
                ) : (
                  <div className="text-sm text-neutral-500">
                    No investigations yet. Start one from the Opportunities page.
                  </div>
                )}
              </div>
            </div>

            <RecentInvestigations investigations={investigations} />
          </div>
        </div>
      </div>
    </main>
  );
}
