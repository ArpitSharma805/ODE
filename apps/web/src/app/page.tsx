"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import {
  Search,
  ArrowRight,
  Loader2,
  Activity,
  Printer,
  FileText,
  FileJson,
  AlertCircle,
  Home as HomeIcon,
  Clock,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { PipelineAnalysis } from "@/components/pipeline-analysis";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";

const EXAMPLE_PROMPTS = [
  "What opportunities exist in MCP?",
  "What opportunities exist in LangGraph?",
  "Is React worth learning in 2026?",
  "Is Rust worth learning in 2026?",
  "Which DevOps tools are growing fastest?",
];

const AGENT_ORDER = [
  "Intent Analyzer",
  "Signal Analyst",
  "Trend Analyst",
  "Opportunity Analyst",
  "Report Agent",
];

interface Opportunity {
  opportunity_id: number;
  title: string;
  category?: string;
  score: number;
  lifecycle_state: string;
  score_components?: Record<string, number | string | string[]>;
  recommended_action?: string;
  why_now?: string;
  supporting_evidence?: string;
  description?: string;
  summary?: string;
  why_existing_solutions_fail?: string;
  business_model?: string;
  risk_assessment?: string;
  who_benefits?: string;
  execution_roadmap?: {
    phase_1?: string;
    phase_2?: string;
    phase_3?: string;
    build_complexity?: string;
  };
}

interface Theme {
  name?: string;
  summary?: string;
}

interface MCPCall {
  server: string;
  tool: string;
  success?: boolean;
  duration?: number | null;
  error?: string;
}

interface AgentStatus {
  status: "pending" | "running" | "completed" | "failed";
  duration?: number | null;
  detail?: string;
  mcp_calls?: MCPCall[];
  discovered_repos?: string[];
  expanded_queries?: string[];
  signals?: number;
  trends?: number;
  trend_count?: number;
  opportunities?: number;
  ranked?: number;
  signal_clusters?: Record<string, number>;
  entity_disambiguation?: {
    original_entity: string;
    disambiguated_entity: string;
    confidence: number;
    supporting_signals: string[];
  };
  llm_calls?: number;
  signals_collected?: number;
}

interface Signal {
  metric: string;
  entity?: string;
  value?: string;
  evidence_quality?: number;
  source_type?: string;
  source?: string;
  source_url?: string;
  raw_metadata?: {
    timestamp?: string;
  };
}

interface StreamEvent {
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
  opportunity?: Opportunity;
  opportunities?: Opportunity[];
  top_opportunity?: Opportunity;
  answer?: { answer?: string };
  signals?: Signal[];
  discovered_repos?: string[];
  synthesis?: {
    themes?: { name?: string; summary?: string }[];
    insights?: { statement?: string }[];
    problems?: { statement?: string }[];
    narrative?: string;
  } | null;
}

interface Investigation {
  investigation_id: number;
  query: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  completed_at?: string | null;
  final_state?: StreamEvent | null;
  agent_states?: Record<string, AgentStatus>;
  trace_log?: StreamEvent[];
  pipeline_artifacts?: Record<string, unknown>;
  error?: string | null;
}

function renderRiskAssessment(riskAssessment: string | undefined) {
  if (!riskAssessment) return null;

  // Try to parse as JSON if it looks like JSON
  if (riskAssessment.trim().startsWith('{')) {
    try {
      const riskObj = JSON.parse(riskAssessment);
      if (riskObj.key_risks || riskObj.mitigations) {
        return (
          <div className="space-y-2">
            {riskObj.summary && (
              <div className="text-sm text-muted-foreground">{riskObj.summary}</div>
            )}
            {riskObj.key_risks && (
              <div className="mb-2">
                <div className="text-xs font-medium text-foreground mb-1">Key Risks:</div>
                <ul className="list-disc list-inside space-y-1">
                  {Array.isArray(riskObj.key_risks) ? riskObj.key_risks.map((risk: string, idx: number) => (
                    <li key={idx} className="text-sm text-muted-foreground">{risk}</li>
                  )) : null}
                </ul>
              </div>
            )}
            {riskObj.mitigations && (
              <div>
                <div className="text-xs font-medium text-foreground mb-1">Mitigations:</div>
                <ul className="list-disc list-inside space-y-1">
                  {Array.isArray(riskObj.mitigations) ? riskObj.mitigations.map((mit: string, idx: number) => (
                    <li key={idx} className="text-sm text-muted-foreground">{mit}</li>
                  )) : null}
                </ul>
              </div>
            )}
          </div>
        );
      }
    } catch {
      // If parsing fails, fall through to plain text
    }
  }

  // Fallback: display as plain text
  return <div className="text-sm text-muted-foreground">{riskAssessment}</div>;
}

function getDeveloperActivityLevel(signals: Signal[]): 'Low' | 'Medium' | 'High' {
  if (!signals || signals.length === 0) return 'Low';

  // Count code, repository, commit, issue, and package signals
  const devSignals = signals.filter(s => {
    const src = (s.source_type || s.source || '').toLowerCase();
    const metric = (s.metric || '').toLowerCase();
    const url = (s.source_url || '').toLowerCase();
    return (
      src.includes('github') ||
      metric.includes('github') ||
      metric.includes('commit') ||
      metric.includes('repo') ||
      metric.includes('code') ||
      url.includes('github.com') ||
      url.includes('gitlab.com') ||
      url.includes('npmjs.com') ||
      url.includes('pypi.org')
    );
  });

  // Check if any high-engagement signal exists (e.g. stars > 50 or commits > 5)
  const hasHighMetric = devSignals.some(s => {
    const val = parseInt(s.value || '0', 10);
    return !isNaN(val) && val >= 50;
  });

  if (devSignals.length >= 4 || (devSignals.length >= 2 && hasHighMetric)) return 'High';
  if (devSignals.length >= 2 || hasHighMetric || signals.length >= 6) return 'Medium';
  return 'Low';
}

function getCommunityInterestLevel(signals: Signal[]): 'Low' | 'Medium' | 'High' {
  if (!signals || signals.length === 0) return 'Low';

  // Count discussion, social, forum, and community mentions
  const communitySignals = signals.filter(s => {
    const src = (s.source_type || s.source || '').toLowerCase();
    const metric = (s.metric || '').toLowerCase();
    const url = (s.source_url || '').toLowerCase();
    return (
      src.includes('hacker') ||
      src.includes('hn') ||
      src.includes('reddit') ||
      src.includes('producthunt') ||
      metric.includes('discussion') ||
      metric.includes('comment') ||
      url.includes('ycombinator.com') ||
      url.includes('reddit.com')
    );
  });

  if (communitySignals.length >= 3 || signals.length >= 8) return 'High';
  if (communitySignals.length >= 1 || signals.length >= 4) return 'Medium';
  return 'Low';
}

function getIndustryAttentionLevel(signals: Signal[]): 'Low' | 'Medium' | 'High' {
  if (!signals || signals.length === 0) return 'Low';

  // Count industry news, research, benchmarks, and enterprise search results
  const industrySignals = signals.filter(s => {
    const src = (s.source_type || s.source || '').toLowerCase();
    const metric = (s.metric || '').toLowerCase();
    const url = (s.source_url || '').toLowerCase();
    return (
      src.includes('tavily') ||
      src.includes('news') ||
      src.includes('web') ||
      metric.includes('market') ||
      metric.includes('search') ||
      metric.includes('web_page') ||
      url.includes('techcrunch.com') ||
      url.includes('venturebeat.com') ||
      url.includes('blog')
    );
  });

  if (industrySignals.length >= 4 || signals.length >= 7) return 'High';
  if (industrySignals.length >= 2 || signals.length >= 3) return 'Medium';
  return 'Low';
}

function cleanSignalTitle(entity: string): string {
  // Remove URL in parentheses at end: "title (https://...)" or "title ("
  let title = entity.replace(/\s*\(https?:\/\/[^)]*\)?\s*$/, '').trim();
  // Remove any remaining trailing "("
  title = title.replace(/\s*\(\s*$/, '').trim();
  // Remove site name after | if title is long enough without it
  const pipeIndex = title.lastIndexOf('|');
  if (pipeIndex > 30) {
    title = title.substring(0, pipeIndex).trim();
  }
  // Capitalize first letter of each word for short titles, or just first letter for long ones
  if (title.length < 60) {
    return title.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }
  return title.charAt(0).toUpperCase() + title.slice(1);
}

function getSourceLabel(signal: Signal): { label: string; color: string } {
  const type = (signal.source_type || signal.metric || '').toLowerCase();
  if (type.includes('tavily')) return { label: 'Web', color: '#3B82F6' };
  if (type.includes('github')) return { label: 'GitHub', color: '#6B7280' };
  if (type.includes('hackernews') || type.includes('hn')) return { label: 'Hacker News', color: '#F97316' };
  if (type.includes('reddit')) return { label: 'Reddit', color: '#EF4444' };
  if (type.includes('jobs') || type.includes('adzuna')) return { label: 'Jobs', color: '#10B981' };
  if (type.includes('web') || type.includes('playwright')) return { label: 'Web', color: '#8B5CF6' };
  if (type.includes('producthunt')) return { label: 'Product Hunt', color: '#F59E0B' };
  if (type.includes('news')) return { label: 'News', color: '#06B6D4' };
  return { label: 'Signal', color: '#6B7280' };
}

function formatSignalDate(timestamp: string | number | undefined | null): string | null {
  if (!timestamp || timestamp === 'Unknown' || timestamp === 'unknown') return null;
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return null;
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
    if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo ago`;
    return `${Math.floor(diffDays / 365)}y ago`;
  } catch {
    return null;
  }
}

function cleanSnippet(text: string): string {
  if (!text) return '';
  return text
    .replace(/#{1,6}\s*/g, '')           // Remove markdown headers
    .replace(/\*{1,3}/g, '')             // Remove bold/italic markers
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')  // Convert links to text
    .replace(/^\|.*$/gm, '')             // Remove table rows
    .replace(/^[-=]+$/gm, '')            // Remove horizontal rules
    .replace(/`{1,3}/g, '')              // Remove code markers
    .replace(/\n+/g, ' ')               // Collapse newlines
    .replace(/\s+/g, ' ')               // Collapse whitespace
    .trim()
    .slice(0, 150);
}

function extractUrl(signal: Signal): string | null {
  if (signal.source_url) return signal.source_url;
  const match = (signal.entity || '').match(/\((https?:\/\/[^)]+)\)/);
  return match ? match[1] : null;
}

function renderLatestSignals(signals: Signal[], query: string) {
  // Handle empty or undefined signals
  if (!signals || !Array.isArray(signals) || signals.length === 0) {
    return [];
  }

  const articles = signals
    .filter(s => {
      // Only include high-quality signals
      if (!s.source_url) return false;
      if ((s.evidence_quality || 0) < 50) return false; // Only high-quality signals

      // Filter out generic/unrelated HN results
      if (s.source?.toLowerCase().includes('hackernews') || s.metric?.toLowerCase().includes('hackernews')) {
        const title = (s.entity || '').toLowerCase();
        // Exclude if title doesn't contain query-related terms
        const queryTerms = query.toLowerCase().split(' ').filter(t => t.length > 2);
        const hasRelevantTerm = queryTerms.some(term => title.includes(term));
        if (!hasRelevantTerm) return false;
      }

      return true;
    })
    .map(s => ({
      signal: s,
      title: cleanSignalTitle(s.entity || 'Unknown'),
      url: s.source_url || '',
      source: getSourceLabel(s),
      date: formatSignalDate(s.raw_metadata?.timestamp),
      summary: cleanSnippet(s.value || ''),
      quality: s.evidence_quality || 0
    }))
    .filter(a => a.url) // Only include items with URLs
    .sort((a, b) => (b.quality || 0) - (a.quality || 0)) // Sort by quality
    .slice(0, 5); // Top 5 most relevant

  // Deduplicate by URL
  const seenUrls = new Set();
  const deduplicated = articles.filter(a => {
    if (seenUrls.has(a.url)) return false;
    seenUrls.add(a.url);
    return true;
  });

  return deduplicated;
}

function safeFilename(text: string) {
  return text
    .slice(0, 40)
    .replace(/\W+/g, "-")
    .replace(/^-|-$/g, "");
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [states, setStates] = useState<Record<string, AgentStatus>>({});
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [answer, setAnswer] = useState<string | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [discoveredRepos, setDiscoveredRepos] = useState<string[]>([]);
  const [synthesis, setSynthesis] = useState<StreamEvent['synthesis']>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentInvestigationId, setCurrentInvestigationId] = useState<number | null>(null);
  const [showPipelineView, setShowPipelineView] = useState(false);
  const [pipelineStartTime, setPipelineStartTime] = useState<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const eventsQueueRef = useRef<StreamEvent[]>([]);
  const router = useRouter();
  const processingRef = useRef(false);
  const statesRef = useRef<Record<string, AgentStatus>>({});
  const completedRef = useRef(false);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const restoreFromInvestigation = useCallback((inv: Investigation) => {
    setQuery(inv.query);
    setSubmitted(true);
    setLoading(false);
    setStreaming(inv.status === "running");
    setError(inv.error || null);

    const final = inv.final_state;
    if (final) {
      const next = final.status || inv.agent_states || {};
      statesRef.current = next;
      setStates(next);
      setOpportunities(final.opportunities || []);

      // Handle answer field - could be ChatResponse object, dict, or serialized format
      let answerContent: string | null = null;
      if (final.answer) {
        if (typeof final.answer === 'object' && 'answer' in final.answer && typeof final.answer.answer === 'string') {
          answerContent = final.answer.answer;
        } else if (typeof final.answer === 'string') {
          answerContent = final.answer;
        } else if (typeof final.answer === 'object' && 'type' in final.answer && final.answer.type === 'text' && 'content' in final.answer) {
          answerContent = final.answer.content as string;
        } else if (typeof final.answer === 'object' && 'type' in final.answer && final.answer.type === 'structured' && 'data' in final.answer && final.answer.data && typeof final.answer.data === 'object' && 'answer' in final.answer.data) {
          answerContent = (final.answer.data.answer as string) || null;
        }
      }
      setAnswer(answerContent);

      setSignals(final.signals || []);
      setDiscoveredRepos(final.discovered_repos || []);
      setSynthesis(final.synthesis || null);
    } else if (inv.agent_states) {
      statesRef.current = inv.agent_states;
      setStates(inv.agent_states);
    }
  }, []);

  const loadInvestigation = useCallback(async (id: number | string) => {
    const sanitizedId = String(id).split(":")[0];
    try {
      const res = await fetch(`/api/investigations/${sanitizedId}`);
      if (!res.ok) return;
      const inv = (await res.json()) as Investigation;
      restoreFromInvestigation(inv);
      if (inv.status === "running") {
        stopPolling();
        pollRef.current = window.setInterval(async () => {
          const pollRes = await fetch(`/api/investigations/${sanitizedId}`);
          if (!pollRes.ok) return;
          const updated = (await pollRes.json()) as Investigation;
          if (updated.status !== "running") {
            stopPolling();
            restoreFromInvestigation(updated);
          } else if (updated.agent_states) {
            const next = updated.agent_states;
            if (next !== statesRef.current) {
              statesRef.current = next;
              setStates(next);
            }
          }
        }, 2000);
      }
    } catch (e) {
      console.error("Failed to restore investigation:", e);
    }
  }, [restoreFromInvestigation, stopPolling]);

  // Cleanup EventSource on unmount to prevent memory leaks
  useEffect(() => {
    return () => {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      if (cancelled) return;
      const params = new URLSearchParams(window.location.search);
      const idFromUrl = params.get("investigation");
      // Only load from storage if there's no URL parameter
      const idFromStorage = idFromUrl ? null : window.localStorage.getItem("ode-current-investigation-id");
      const id = idFromUrl ? parseInt(idFromUrl, 10) : idFromStorage ? parseInt(idFromStorage, 10) : null;
      if (id && !Number.isNaN(id)) {
        await loadInvestigation(id);
      }
    };
    const timer = window.setTimeout(init, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      esRef.current?.close();
      stopPolling();
    };
  }, [loadInvestigation, stopPolling]);

  const submit = (text: string) => {
    console.log("[submit] called with:", text);
    // Clear stored investigation ID when starting a new query
    window.localStorage.removeItem("ode-current-investigation-id");
    if (esRef.current) {
      console.log("[submit] closing existing EventSource");
      esRef.current.close();
      esRef.current = null;
    }
    eventsQueueRef.current = [];
    processingRef.current = false;
    statesRef.current = {};
    completedRef.current = false;
    setQuery(text);
    setSubmitted(true);
    setLoading(true);
    setStreaming(true);
    setOpportunities([]);
    setAnswer(null);
    setSignals([]);
    setDiscoveredRepos([]);
    setSynthesis(null);
    setStates({});
    setError(null);
    setPipelineStartTime(Date.now());

    const sanitizedQuery = text.trim().replace(/[\r\n\t]+/g, " ");
    // Use URLSearchParams for proper URL parameter encoding
    const params = new URLSearchParams();
    params.set("query", sanitizedQuery);
    params.set("seed_only", "false");
    const sseUrl = `/api/query/stream?${params.toString()}`;
    console.log("[submit] FULL URL:", sseUrl);
    let es: EventSource;
    try {
      es = new EventSource(sseUrl);
    } catch (err) {
      console.error("[submit] EventSource constructor threw", err);
      setError("Failed to open stream");
      setLoading(false);
      setStreaming(false);
      return;
    }
    esRef.current = es;

    let intentionallyClosed = false;

    // Give the backend a reasonable window to accept the SSE connection.
    const connectionTimeout = window.setTimeout(() => {
      if (completedRef.current || intentionallyClosed) return;
      if (es.readyState !== EventSource.OPEN) {
        console.error("[EventSource] connection timeout", sseUrl);
        intentionallyClosed = true;
        es.close();
        esRef.current = null;
        setStreaming(false);
        setLoading(false);
        setError("Connection timed out. The analysis backend is not responding.");
      }
    }, 30000);

    es.onopen = () => {
      console.log("[EventSource] opened", sseUrl);
      window.clearTimeout(connectionTimeout);
    };
    es.onerror = () => {
      window.clearTimeout(connectionTimeout);
      if (completedRef.current || intentionallyClosed) return;
      // EventSource may fire onerror while reconnecting (readyState 0).
      // Only treat a fully-closed connection as a failure.
      if (es.readyState !== EventSource.CLOSED) {
        console.warn(
          "[EventSource] transient error/reconnect readyState=",
          es.readyState,
          sseUrl
        );
        return;
      }
      console.error(
        "[EventSource] error readyState=",
        es.readyState,
        sseUrl
      );
      intentionallyClosed = true;
      esRef.current = null;
      es.close();
      setStreaming(false);
      setLoading(false);
      setError((prev) => prev || "Stream closed unexpectedly");
    };
    es.onmessage = (event: MessageEvent) => {
      window.clearTimeout(connectionTimeout);
      console.log("[EventSource] message", event.data?.slice(0, 200));
      try {
        const data = JSON.parse(event.data) as StreamEvent;
        if (data.type === "heartbeat") return;
        if (data.type === "created" && data.investigation_id) {
          window.localStorage.setItem("ode-current-investigation-id", String(data.investigation_id));
        }
        if (data.type === "final") {
          completedRef.current = true;
          if (data.investigation_id) {
            window.localStorage.setItem("ode-current-investigation-id", String(data.investigation_id));
          }
        }
        eventsQueueRef.current.push(data);
        scheduleProcess();
      } catch (e) {
        console.error("Failed to parse stream:", e);
        intentionallyClosed = true;
        esRef.current = null;
        es.close();
        setError("Failed to parse stream");
        setStreaming(false);
        setLoading(false);
      }
    };

    const processEvent = (data: StreamEvent) => {
      if (data.type === "heartbeat") return;

      if (data.type === "created" && data.investigation_id) {
        window.localStorage.setItem("ode-current-investigation-id", String(data.investigation_id));
        setCurrentInvestigationId(data.investigation_id);
        return;
      }

      if (data.type === "status" || data.type === "update") {
        const next = data.status || {};
        statesRef.current = next;

        setStates(next);
      } else if (data.type === "opportunity" && data.opportunity) {
        setOpportunities((prev) => [data.opportunity as Opportunity, ...prev]);
      } else if (data.type === "answer" && data.answer?.answer) {
        setAnswer(data.answer?.answer || null);
      } else if (data.type === "final") {
        completedRef.current = true;
        window.clearTimeout(connectionTimeout);
        const next = data.status || {};
        statesRef.current = next;

        setStates(next);
        setOpportunities(data.opportunities || []);
        setAnswer(data.answer?.answer || null);
        setSignals(data.signals || []);
        setDiscoveredRepos(data.discovered_repos || []);
        setSynthesis(data.synthesis || null);
        setStreaming(false);
        setLoading(false);
        intentionallyClosed = true;
        esRef.current = null;
        es.close();
      } else if (data.type === "error") {
        window.clearTimeout(connectionTimeout);
        setError(data.message || "Pipeline failed");
        setStreaming(false);
        setLoading(false);
        intentionallyClosed = true;
        esRef.current = null;
        es.close();
      }
    };

    const scheduleProcess = () => {
      if (processingRef.current) return;
      processingRef.current = true;
      window.setTimeout(() => {
        processingRef.current = false;
        const event = eventsQueueRef.current.shift();
        if (event) processEvent(event);
        if (eventsQueueRef.current.length > 0) scheduleProcess();
      }, 0);
    };
  };

  const resetAndGoHome = useCallback(() => {
    // Close any active EventSource connection
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    // Reset all state
    setQuery("");
    setSubmitted(false);
    setLoading(false);
    setStreaming(false);
    setError(null);
    setAnswer(null);
    setSignals([]);
    setDiscoveredRepos([]);
    setSynthesis(null);
    setOpportunities([]);
    setStates({});
    // Clear stored investigation ID
    window.localStorage.removeItem("ode-current-investigation-id");
    // Scroll to top
    window.scrollTo(0, 0);
    // Navigate to home using Next.js router
    router.push("/");
  }, [router]);

  const stage = !submitted
    ? "hero"
    : error
      ? "error"
      : streaming
        ? "trace"
        : "report";

  return (
    <main className="relative min-h-screen overflow-x-hidden bg-transparent text-foreground">

      <AnimatePresence mode="wait">
        <motion.div
          key={stage}
          initial={{ opacity: 0, y: 60 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -60, scale: 0.96 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] as const }}
        >
          {stage === "hero" && (
            <>
              <Hero
                query={query}
                setQuery={setQuery}
                loading={loading}
                onSubmit={(text) => submit(text)}
              />
              <footer className="relative z-10 mx-auto mt-12 max-w-7xl px-6 pb-8 text-center print-hidden">
                <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted-foreground">
                  Powered by
                </p>
                <div className="flex items-center justify-center gap-3 text-sm text-muted-foreground">
                  <span>GitHub</span>
                  <span>•</span>
                  <span>Hacker News</span>
                  <span>•</span>
                  <span>Tavily</span>
                </div>
              </footer>
            </>
          )}
          {stage === "trace" && (
            <div className="relative z-10 mx-auto max-w-2xl px-6 pt-8 pb-16 print-hidden">
              <LiveProgressTracker
                agents={states}
                query={query}
                pipelineStartTime={pipelineStartTime}
                onCancel={resetAndGoHome}
              />
            </div>
          )}
          {stage === "error" && (
            <div className="relative z-10 mx-auto max-w-3xl px-6 pt-8 pb-16 print-hidden">
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-3 rounded-2xl border border-destructive/20 bg-destructive/10 p-4 text-destructive">
                  <AlertCircle className="h-5 w-5" />
                  {error}
                </div>
                <Button
                  onClick={resetAndGoHome}
                  variant="outline"
                  className="w-fit"
                >
                  ← Back to Home
                </Button>
              </div>
            </div>
          )}
          {stage === "report" && (
            <Report
              query={query}
              answer={answer}
              topOpportunity={opportunities[0] || null}
              opportunities={opportunities}
              states={states}
              signals={signals}
              discoveredRepos={discoveredRepos}
              synthesis={synthesis}
              onSubmit={submit}
              investigationId={currentInvestigationId}
              showPipelineView={showPipelineView}
              onTogglePipelineView={() => setShowPipelineView(!showPipelineView)}
              onResetAndGoHome={resetAndGoHome}
            />
          )}
        </motion.div>
      </AnimatePresence>
    </main>
  );
}

function SearchBox({
  query,
  setQuery,
  loading,
  onSubmit,
  placeholder,
  className = "",
}: {
  query: string;
  setQuery: (q: string) => void;
  loading: boolean;
  onSubmit: (q: string) => void;
  placeholder: string;
  className?: string;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = query.trim();
        if (trimmed && !loading) onSubmit(trimmed);
      }}
      className={className}
    >
      <div className="relative">
        <Search className="absolute left-5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          className="h-16 w-full rounded-full border border-neutral-300/90 bg-white pl-14 pr-16 text-lg shadow-sm ring-foreground/20 focus-visible:ring-1 dark:border-neutral-700 dark:bg-[#14161A]"
        />
        <Button
          type="submit"
          disabled={loading || !query.trim()}
          className="absolute right-2 top-1/2 h-12 -translate-y-1/2 rounded-full px-6"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ArrowRight className="h-4 w-4" />
          )}
        </Button>
      </div>
    </form>
  );
}

function ChatBox({
  query,
  setQuery,
  loading,
  onSubmit,
  className = "",
}: {
  query: string;
  setQuery: (q: string) => void;
  loading: boolean;
  onSubmit: (q: string) => void;
  className?: string;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = query.trim();
        if (trimmed && !loading) onSubmit(trimmed);
      }}
      className={className}
    >
      <div className="relative flex flex-col rounded-[2rem] border border-neutral-300/90 bg-white p-8 shadow-lg shadow-foreground/5 focus-within:ring-1 focus-within:ring-foreground/20 dark:border-neutral-700 dark:bg-[#14161A]">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              const trimmed = query.trim();
              if (trimmed && !loading) onSubmit(trimmed);
            }
          }}
          placeholder="Ask ODE about technologies, trends, and opportunities..."
          rows={3}
          className="min-h-[110px] w-full resize-none rounded-2xl border-0 bg-transparent p-4 text-lg text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-0"
        />
        <div className="flex items-center justify-between px-3 pb-1 pt-4">
          <span className="text-sm text-muted-foreground">
            {query.length > 0 && "Shift + Enter for a new line"}
          </span>
          <Button
            type="submit"
            disabled={loading || !query.trim()}
            className="h-14 rounded-full px-10 bg-primary text-primary-foreground hover:bg-primary/90"
          >
            {loading ? (
              <Loader2 className="h-6 w-6 animate-spin" />
            ) : (
              <span className="flex items-center gap-2">
                Ask
                <ArrowRight className="h-6 w-6" />
              </span>
            )}
          </Button>
        </div>
      </div>
    </form>
  );
}

function Hero({
  query,
  setQuery,
  loading,
  onSubmit,
}: {
  query: string;
  setQuery: (q: string) => void;
  loading: boolean;
  onSubmit: (q: string) => void;
}) {
  return (
    <section className="relative z-10 mx-auto grid min-h-[80vh] max-w-7xl grid-cols-1 items-center gap-12 px-6 pb-16 pt-6 lg:grid-cols-[7fr_5fr] lg:gap-24 print-hidden">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="lg:pr-12"
      >
        <div className="header-scrim p-8 border border-neutral-300/90 dark:border-neutral-700/80 shadow-sm">
          <h1 className="text-4xl font-bold tracking-tight text-foreground md:text-5xl lg:text-6xl">
            Opportunity Discovery Engine
          </h1>
          <p className="mt-8 max-w-lg text-lg leading-relaxed text-foreground/80">
            ODE analyzes GitHub, Hacker News, documentation, and technology signals to identify emerging trends, learning paths, and market opportunities.
          </p>
          <div className="mt-12">
            <p className="mb-4 text-xs font-medium uppercase tracking-widest text-muted-foreground">
              What can you uncover
            </p>
            <div className="flex flex-wrap gap-2">
              {[
                "Technology opportunities",
                "Open-source ecosystems",
                "Learning roadmaps",
                "AI agents & MCPs",
                "Developer trends",
                "Market intelligence"
              ].map((topic) => (
                <span
                  key={topic}
                  className="inline-flex items-center rounded-full border border-neutral-300/90 bg-white px-3 py-1.5 text-sm text-muted-foreground dark:border-neutral-700 dark:bg-[#14161A]"
                >
                  {topic}
                </span>
              ))}
            </div>
          </div>
        </div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.1 }}
        className="w-full max-w-[60rem] mt-8"
      >
        <motion.div
          animate={{
            boxShadow: [
              "0 0 0 0px rgba(99, 102, 241, 0)",
              "0 0 0 8px rgba(99, 102, 241, 0)",
              "0 0 0 0px rgba(99, 102, 241, 0)",
            ],
          }}
          transition={{
            duration: 2,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        >
          <ChatBox
            query={query}
            setQuery={setQuery}
            loading={loading}
            onSubmit={onSubmit}
          />
        </motion.div>
        <div className="mt-8">
          <p className="mb-3 text-sm font-medium text-foreground/60">Try asking:</p>
          <div className="flex flex-wrap gap-2 print-hidden">
            {EXAMPLE_PROMPTS.slice(0, 4).map((prompt) => (
              <button
                key={prompt}
                type="button"
                disabled={loading}
                onClick={() => onSubmit(prompt)}
                className="rounded-full border border-neutral-300/90 bg-white px-4 py-2 text-sm text-muted-foreground transition-colors hover:border-neutral-400 dark:border-neutral-700 dark:bg-[#14161A] dark:hover:border-neutral-600 disabled:opacity-50"
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>
      </motion.div>
    </section>
  );
}

function LiveProgressTracker({
  agents,
  query,
  pipelineStartTime,
  onCancel,
}: {
  agents: Record<string, AgentStatus>;
  query: string;
  pipelineStartTime: number | null;
  onCancel: () => void;
}) {
  const [elapsedTime, setElapsedTime] = useState(0);

  useEffect(() => {
    if (!pipelineStartTime) return;
    const interval = setInterval(() => {
      setElapsedTime((Date.now() - pipelineStartTime) / 1000);
    }, 100);
    return () => clearInterval(interval);
  }, [pipelineStartTime]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const formatDuration = (seconds: number) => {
    if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = (seconds % 60).toFixed(1);
    return `${mins}m ${secs}s`;
  };

  return (
    <div className="space-y-6">
      {/* Header with query and overall runtime */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-foreground">{query}</h2>
          <Button
            onClick={onCancel}
            variant="outline"
            size="sm"
            className="h-8 px-3 text-xs"
          >
            Cancel
          </Button>
        </div>

        {/* Overall Runtime */}
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Clock className="h-4 w-4" />
          <span className="font-medium">Research Runtime</span>
          <span className="font-mono font-semibold text-foreground">
            {formatTime(elapsedTime)}
          </span>
        </div>
      </div>

      {/* Progress Steps */}
      <div className="space-y-2">
        {AGENT_ORDER.map((name, idx) => {
          const agent = agents[name];
          const status = agent?.status || "pending";
          const duration = agent?.duration;
          const isRunning = status === "running";
          const isCompleted = status === "completed";
          const isFailed = status === "failed";
          const isLast = idx === AGENT_ORDER.length - 1;

          return (
            <div key={name} className="relative">
              {/* Vertical connector line */}
              {!isLast && (
                <div className="absolute left-[21px] top-8 h-8 w-0.5 bg-border/50" />
              )}

              <div className="flex items-center justify-between rounded-lg border border-border/50 bg-card/50 px-4 py-3">
                <div className="flex items-center gap-3">
                  {/* Status Icon */}
                  {isCompleted && (
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-green-500/10 text-green-500">
                      <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  )}
                  {isRunning && (
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500/10 text-blue-500">
                      <Loader2 className="h-3 w-3 animate-spin" />
                    </div>
                  )}
                  {isFailed && (
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500/10 text-red-500">
                      <AlertCircle className="h-3 w-3" />
                    </div>
                  )}
                  {status === "pending" && (
                    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-muted-foreground">
                      <div className="h-2 w-2 rounded-full bg-muted-foreground/50" />
                    </div>
                  )}

                  {/* Agent Name */}
                  <span className={`text-sm font-medium ${isRunning ? "text-foreground" : "text-muted-foreground"}`}>
                    {name}
                  </span>
                </div>

                {/* Timing and Status */}
                <div className="flex items-center gap-3">
                  {isRunning && (
                    <span className="text-sm text-blue-500">
                      Running...
                    </span>
                  )}
                  {isCompleted && duration !== undefined && duration !== null && (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-muted-foreground">
                        {formatDuration(Number(duration))}
                      </span>
                      {agent?.llm_calls && agent.llm_calls > 0 && (
                        <Badge variant="outline" className="text-xs">
                          {agent.llm_calls} LLM
                        </Badge>
                      )}
                      {agent?.signals_collected && agent.signals_collected > 0 && (
                        <Badge variant="outline" className="text-xs">
                          {agent.signals_collected} signals
                        </Badge>
                      )}
                    </div>
                  )}
                  {isFailed && (
                    <span className="text-sm text-red-500">
                      Failed
                    </span>
                  )}
                  {status === "pending" && (
                    <span className="text-sm text-muted-foreground">
                      Waiting...
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}

        {/* Total Duration */}
        {Object.values(agents).some(a => a?.status === "completed") && (
          <div className="mt-4 rounded-lg border border-border/50 bg-muted/30 px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-muted-foreground">Total Pipeline Duration</span>
              <span className="text-sm font-semibold text-foreground">
                {formatDuration(
                  Object.values(agents)
                    .filter(a => a?.duration !== undefined && a?.duration !== null)
                    .reduce((sum, a) => sum + (a?.duration || 0), 0)
                )}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Report({
  query,
  answer,
  topOpportunity,
  opportunities,
  states,
  signals,
  discoveredRepos,
  synthesis,
  onSubmit,
  investigationId,
  showPipelineView,
  onTogglePipelineView,
  onResetAndGoHome,
}: {
  query: string;
  answer: string | null;
  topOpportunity: Opportunity | null;
  opportunities: Opportunity[];
  states: Record<string, AgentStatus>;
  signals: Signal[];
  discoveredRepos: string[];
  synthesis: StreamEvent['synthesis'] | null;
  onSubmit: (text: string) => void;
  investigationId: number | null;
  showPipelineView: boolean;
  onTogglePipelineView: () => void;
  onResetAndGoHome: () => void;
}) {
  const generatedAt = useMemo(() => new Date().toLocaleString(), []);
  const [followUp, setFollowUp] = useState("");

  const rawStates = states as Record<string, unknown>;
  const intentType = ((rawStates.intent as { intent?: string } | undefined)?.intent) ?? "Opportunity Discovery";
  const isOpportunity = true;  // Always use opportunity rendering path for all intent types

  const reportTypeLabel = {
    "Skill Learning": "Learning Roadmap",
    "Career Development": "Career Guidance",
    "Technology Evaluation": "Technology Evaluation",
    "Market Intelligence": "Market Intelligence",
    "Opportunity Discovery": "Opportunity Analysis",
    "Product Ideas": "Product Opportunities",
    "Business Opportunities": "Business Opportunities",
  }[intentType] || "Analysis";

  const exportMarkdown = () => {
    if (!answer) return;
    const blob = new Blob([answer], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ode-report-${safeFilename(query)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportJSON = () => {
    const payload = {
      query,
      generatedAt,
      top_opportunity: topOpportunity,
      opportunities,
      signals,
      discovered_repos: discoveredRepos,
      states,
      answer,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ode-report-${safeFilename(query)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <section className="relative z-10 mx-auto max-w-6xl px-6 pt-8 pb-16">
        <header className="header-scrim p-8 border border-neutral-300/90 dark:border-neutral-700/80 mb-12 shadow-sm">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-neutral-300/90 bg-white px-3 py-1 text-xs font-medium uppercase tracking-widest text-muted-foreground dark:border-neutral-700 dark:bg-[#14161A]">
            <span>ODE Intelligence</span>
            <span className="text-muted-foreground/60">·</span>
            <span>{reportTypeLabel}</span>
          </div>
          <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <div className="max-w-3xl">
              <h1 className="text-4xl font-semibold leading-tight tracking-tight text-foreground md:text-5xl">
                {query}
              </h1>
              <p className="mt-3 text-sm text-muted-foreground">
                Generated {generatedAt}
              </p>
            </div>
            <div className="flex flex-wrap gap-3 print-hidden">
              <Button
                variant="outline"
                onClick={onResetAndGoHome}
                className="gap-2"
              >
                <HomeIcon className="h-4 w-4" />
                Home
              </Button>
              <Button
                variant="outline"
                onClick={() => window.print()}
                className="gap-2"
              >
                <Printer className="h-4 w-4" />
                Print Report
              </Button>
              {answer && (
                <Button variant="ghost" onClick={exportMarkdown} className="gap-2">
                  <FileText className="h-4 w-4" />
                  Markdown
                </Button>
              )}
              <Button variant="ghost" onClick={exportJSON} className="gap-2">
                <FileJson className="h-4 w-4" />
                JSON
              </Button>
              {investigationId && (
                <Button
                  variant={showPipelineView ? "default" : "outline"}
                  onClick={onTogglePipelineView}
                  className="gap-2"
                >
                  <Activity className="h-4 w-4" />
                  {showPipelineView ? "Hide Analysis" : "Show Analysis"}
                </Button>
              )}
            </div>
          </div>

          <div className="mt-10 max-w-2xl print-hidden">
            <SearchBox
              query={followUp}
              setQuery={setFollowUp}
              loading={false}
              onSubmit={(text) => {
                setFollowUp("");
                onSubmit(text);
              }}
              placeholder="Ask another question..."
            />
          </div>
        </header>

        {/* Opportunity Analysis - Opportunity-focused view */}
        {isOpportunity && topOpportunity && (
          <div className="space-y-8">
            {/* Top Opportunity */}
            <Card className="card-base">
              <CardContent className="p-6 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="flex h-2 w-2 rounded-full bg-emerald-500" />
                    <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                      Top Opportunity
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                      Stage: <strong className="text-neutral-900 dark:text-white">{topOpportunity.lifecycle_state || "Emerging"}</strong>
                    </span>
                    <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300 border border-blue-200/50 dark:border-blue-800/50">
                      Confidence: <strong className="font-semibold">{Number(topOpportunity.score).toFixed(0)}/100</strong>
                    </span>
                  </div>
                </div>

                <h2 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-white">
                  {topOpportunity.title}
                </h2>

                {topOpportunity.description && (
                  <p className="text-sm leading-relaxed text-neutral-600 dark:text-neutral-300">
                    {(() => {
                      const cleanDescription = (topOpportunity.description || "")
                        .replace(/^(\*{0,2}Opportunity Snapshot:?\*{0,2}\s*|:\*{0,2}\s*)/i, "")
                        .replace(new RegExp(`^${topOpportunity.title}\\*{0,2}\\s*`, "i"), "")
                        .trim();
                      return cleanDescription;
                    })()}
                  </p>
                )}
              </CardContent>
            </Card>

            {/* Trend Summary & Market Signals - Side-by-Side Compact Metrics */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Trend Summary */}
              <Card className="card-base">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base font-semibold">Trend Summary</CardTitle>
                    {topOpportunity?.score && (
                      <Badge variant="outline" className="text-xs font-mono">
                        Momentum: {Math.round(topOpportunity.score * 0.9)}/100
                      </Badge>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                    Key Trends
                  </div>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-neutral-600 dark:text-neutral-300">
                    {(() => {
                      let trendMarkdown = "";
                      if (answer && answer.includes("## Trend Summary")) {
                        trendMarkdown = answer.split("## Trend Summary")[1]?.split("##")[0]?.trim() || "";
                      }

                      if (trendMarkdown && trendMarkdown.length > 20) {
                        return <ReactMarkdown>{trendMarkdown}</ReactMarkdown>;
                      }

                      if (synthesis?.themes && synthesis.themes.length > 0) {
                        return (
                          <ul className="space-y-2 list-disc pl-4">
                            {synthesis.themes.slice(0, 4).map((theme: Theme, idx: number) => (
                              <li key={idx}>
                                <strong className="text-neutral-900 dark:text-white">{theme.name || "Ecosystem Movement"}:</strong> {theme.summary}
                              </li>
                            ))}
                          </ul>
                        );
                      }

                      return (
                        <ul className="space-y-2 list-disc pl-4">
                          <li><strong className="text-neutral-900 dark:text-white">Growing Protocol Adoption:</strong> Active developer exploration and tool server implementations.</li>
                          <li><strong className="text-neutral-900 dark:text-white">Tooling Ecosystem Gaps:</strong> Demand for standardized security, governance, and debugging infrastructure.</li>
                        </ul>
                      );
                    })()}
                  </div>
                </CardContent>
              </Card>

              {/* Market Signals */}
              <Card className="card-base">
                <CardHeader>
                  <CardTitle className="text-base font-medium">Market Signals</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div>
                      <div className="mb-2 text-sm font-medium text-foreground">Developer Activity</div>
                      <div className="flex items-center gap-2">
                        {getDeveloperActivityLevel(signals) === 'High' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                          </div>
                        ) : getDeveloperActivityLevel(signals) === 'Medium' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        ) : (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-emerald-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        )}
                        <span className="text-sm text-muted-foreground">{getDeveloperActivityLevel(signals)}</span>
                      </div>
                    </div>
                    <div>
                      <div className="mb-2 text-sm font-medium text-foreground">Community Interest</div>
                      <div className="flex items-center gap-2">
                        {getCommunityInterestLevel(signals) === 'High' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                          </div>
                        ) : getCommunityInterestLevel(signals) === 'Medium' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        ) : (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-blue-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        )}
                        <span className="text-sm text-muted-foreground">{getCommunityInterestLevel(signals)}</span>
                      </div>
                    </div>
                    <div>
                      <div className="mb-2 text-sm font-medium text-foreground">Industry Attention</div>
                      <div className="flex items-center gap-2">
                        {getIndustryAttentionLevel(signals) === 'High' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                          </div>
                        ) : getIndustryAttentionLevel(signals) === 'Medium' ? (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        ) : (
                          <div className="flex gap-1">
                            <div className="w-3 h-3 rounded-full bg-purple-500" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                            <div className="w-3 h-3 rounded-full bg-neutral-300 dark:bg-neutral-600" />
                          </div>
                        )}
                        <span className="text-sm text-muted-foreground">{getIndustryAttentionLevel(signals)}</span>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Execution Roadmap */}
            <Card className="card-base">
              <CardHeader>
                <CardTitle className="text-base font-medium">Execution Roadmap</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-6">
                  {([
                    { key: "phase_1", num: "1", defaultTitle: "Foundation & Prototype" },
                    { key: "phase_2", num: "2", defaultTitle: "Development & Integration" },
                    { key: "phase_3", num: "3", defaultTitle: "Launch & Scale" },
                  ] as const).map(({ key, num, defaultTitle }) => {
                    const rawContent = topOpportunity.execution_roadmap?.[key];
                    if (!rawContent) return null;

                    // Extract timeframe if present (e.g., "**Timeframe:** 1-2 months")
                    const timeframeMatch = rawContent.match(/(?:Timeframe|Timeline):\s*([^\n\*]+)/i);
                    const timeframe = timeframeMatch ? timeframeMatch[1].trim() : null;

                    // Clean content by removing the trailing timeframe line
                    const cleanContent = rawContent.replace(/(?:\*\*|\*|-)?\s*(?:Timeframe|Timeline):[^\n]+/gi, "").trim();

                    return (
                      <div
                        key={key}
                        className="rounded-xl border border-neutral-300/90 bg-neutral-50/50 p-5 dark:border-neutral-700/80 dark:bg-[#16181D] space-y-3 transition-colors"
                      >
                        {/* Phase Header */}
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-neutral-200/60 pb-3 dark:border-neutral-800/60">
                          <div className="flex items-center gap-2.5">
                            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-neutral-900 text-xs font-bold text-white dark:bg-white dark:text-neutral-900">
                              {num}
                            </span>
                            <h3 className="text-sm font-semibold text-neutral-900 dark:text-white">
                              {defaultTitle}
                            </h3>
                          </div>
                          {timeframe && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-neutral-200/60 px-2.5 py-0.5 text-xs font-medium text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                              {timeframe}
                            </span>
                          )}
                        </div>

                        {/* Phase Steps */}
                        <div className="prose prose-sm dark:prose-invert max-w-none text-neutral-600 dark:text-neutral-300 [&>ul]:space-y-3 [&>ul]:list-none [&>ul]:pl-0">
                          <ReactMarkdown
                            components={{
                              li: ({ children }) => (
                                <li className="relative pl-6 before:absolute before:left-1 before:top-2 before:h-1.5 before:w-1.5 before:rounded-full before:bg-primary/70 dark:before:bg-primary-400">
                                  {children}
                                </li>
                              ),
                              strong: ({ children }) => (
                                <strong className="font-semibold text-neutral-900 dark:text-white block mb-0.5">
                                  {children}
                                </strong>
                              ),
                              a: ({ ...props }) => (
                                <a {...props} className="text-blue-600 hover:underline dark:text-blue-400 font-normal text-xs inline-block mt-0.5" target="_blank" rel="noopener noreferrer" />
                              )
                            }}
                          >
                            {cleanContent}
                          </ReactMarkdown>
                        </div>
                      </div>
                    );
                  })}

                  {/* Build Complexity Pill */}
                  {topOpportunity.execution_roadmap?.build_complexity && (
                    <div className="flex items-center justify-between pt-2">
                      <span className="text-xs font-medium uppercase tracking-wider text-neutral-500">
                        Build Complexity
                      </span>
                      <span className="rounded-md bg-neutral-100 px-2.5 py-1 text-xs font-semibold text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200">
                        {topOpportunity.execution_roadmap.build_complexity}
                      </span>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Recommendation */}
            <Card className="card-base">
              <CardHeader>
                <CardTitle className="text-base font-medium">Recommendation</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <div className="mb-2 text-sm font-medium text-foreground">Decision</div>
                  <Badge
                    className={
                      Number(topOpportunity.score) >= 70
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300"
                        : Number(topOpportunity.score) >= 40
                        ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                        : "bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300"
                    }
                  >
                    {Number(topOpportunity.score) >= 70 ? 'BUILD' : Number(topOpportunity.score) >= 40 ? 'INVESTIGATE' : 'MONITOR'}
                  </Badge>
                </div>
                <div>
                  <div className="mb-2 text-sm font-medium text-foreground">Supporting Factors</div>
                  <ul className="space-y-3">
                    {topOpportunity.why_now ? (
                      <li className="flex items-start gap-2 text-sm text-neutral-700 dark:text-neutral-300">
                        <span className="text-primary font-bold mt-0.5">•</span>
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                          <ReactMarkdown components={{
                            a: ({...props}) => <a {...props} className="text-blue-600 hover:underline font-medium" target="_blank" rel="noopener noreferrer" />
                          }}>
                            {topOpportunity.why_now}
                          </ReactMarkdown>
                        </div>
                      </li>
                    ) : null}
                    {topOpportunity.risk_assessment ? (
                      <li className="flex items-start gap-2 text-sm text-neutral-700 dark:text-neutral-300">
                        <span className="text-primary font-bold mt-0.5">•</span>
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                          {renderRiskAssessment(topOpportunity.risk_assessment)}
                        </div>
                      </li>
                    ) : null}
                    {!topOpportunity.why_now && !topOpportunity.risk_assessment ? (
                      <li className="text-sm text-muted-foreground">Unable to generate section from available evidence.</li>
                    ) : null}
                  </ul>
                </div>
                <div>
                  <div className="mb-2 text-sm font-medium text-foreground">Next Action</div>
                  <p className="text-sm text-muted-foreground">{topOpportunity.recommended_action || "Unable to generate section from available evidence."}</p>
                </div>
              </CardContent>
            </Card>



            {/* New Scoring Components */}
            <Card className="card-base">
              <CardHeader>
                <CardTitle className="text-base font-medium">Confidence Score</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                {(() => {
                  const sc = topOpportunity.score_components || {};
                  const evidence_strength = typeof sc.evidence_strength === 'number' ? sc.evidence_strength : 0;
                  const momentum = typeof sc.momentum === 'number' ? sc.momentum : 0;
                  const adoption_growth = typeof sc.adoption_growth === 'number' ? sc.adoption_growth : 0;
                  const execution_readiness = typeof sc.execution_readiness === 'number' ? sc.execution_readiness : 0;
                  const overall = typeof sc.total === 'number' ? sc.total : typeof topOpportunity.score === 'number' ? topOpportunity.score : 0;

                  const items = [
                    { label: "Evidence Strength", value: evidence_strength, max: 35, reason: "Based on source diversity, quality, and cross-validation" },
                    { label: "Momentum", value: momentum, max: 25, reason: "Based on repo activity, releases, and ecosystem movement" },
                    { label: "Adoption & Growth", value: adoption_growth, max: 25, reason: "Based on developer adoption and ecosystem participation" },
                    { label: "Execution Readiness", value: execution_readiness, max: 15, reason: "Based on implementation feasibility and documentation maturity" },
                  ];

                  return (
                    <>
                      <div className="border-b border-border pb-5">
                        <div className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                          Overall Score
                        </div>
                        <div className="mt-1 text-5xl font-semibold tracking-tighter text-foreground">
                          {Number(overall).toFixed(0)}
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                          Sum of Evidence Strength + Momentum + Adoption & Growth + Execution Readiness.
                        </p>
                      </div>

                      {items.map((item) => {
                        const value = typeof item.value === 'number' ? item.value : 0;
                        const max = item.max || 100;
                        const percentage = Math.min(100, (value / max) * 100);
                        return (
                          <div key={item.label} className="space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="text-sm text-muted-foreground">{item.label}</span>
                              <span className="text-sm font-medium text-foreground">
                                {value.toFixed(1)}/{max}
                              </span>
                            </div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                              <motion.div
                                className="h-full rounded-full bg-foreground"
                                initial={{ width: 0 }}
                                animate={{ width: `${percentage}%` }}
                                transition={{ duration: 0.8, ease: "easeOut" as const }}
                              />
                            </div>
                            <div className="text-xs text-muted-foreground">{item.reason}</div>
                          </div>
                        );
                      })}
                    </>
                  );
                })()}
              </CardContent>
            </Card>

            {/* Latest Technology Signals - Top 5 */}
            <Card className="card-base">
              <CardHeader>
                <CardTitle className="text-base font-medium">Latest Technology Signals</CardTitle>
              </CardHeader>
              <CardContent>
                {(() => {
                  const deduplicated = renderLatestSignals(signals, query);
                  if (deduplicated.length === 0) {
                    return <p className="text-sm text-muted-foreground">No recent technology signals found.</p>;
                  }

                  return (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {deduplicated.map((article, idx) => {
                        const url = extractUrl(article.signal);
                        const Wrapper = url ? 'a' : 'div';
                        const wrapperProps = url ? { href: url, target: '_blank', rel: 'noopener noreferrer' } : {};

                        return (
                          <Wrapper key={idx} {...wrapperProps} className="block">
                            <div className="border border-neutral-300/90 dark:border-neutral-700/80 rounded-lg p-4 bg-white dark:bg-[#14161A] hover:border-neutral-400 dark:hover:border-neutral-600 transition-colors">
                              <div className="flex items-center justify-between mb-2">
                                <span
                                  className="text-xs font-medium px-2 py-0.5 rounded-full text-white"
                                  style={{ backgroundColor: article.source.color }}
                                >
                                  {article.source.label}
                                </span>
                                {article.date && (
                                  <span className="text-xs text-muted-foreground">
                                    {article.date}
                                  </span>
                                )}
                              </div>
                              <h4 className="font-medium text-sm text-foreground mb-1 line-clamp-2">
                                {article.title}
                              </h4>
                              {article.summary && (
                                <p className="text-xs text-muted-foreground line-clamp-2">
                                  {article.summary}
                                </p>
                              )}
                            </div>
                          </Wrapper>
                        );
                      })}
                    </div>
                  );
                })()}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Non-opportunity reports (Technology Evaluation, Market Intelligence, etc.) */}
        {!isOpportunity && (
          <div className="grid gap-8">
            <Card className="card-base break-inside-avoid">
              <CardHeader>
                <CardTitle className="text-2xl font-semibold tracking-tight">
                  {reportTypeLabel}
                </CardTitle>
                <CardDescription className="text-muted-foreground">
                  Synthesized from live signals and research.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-8 md:p-10">
                <article className="max-w-none">
                  <ReactMarkdown
                    components={{
                      h1: ({ children }) => (
                        <h1 className="mb-6 mt-10 text-3xl font-semibold tracking-tight text-foreground">
                          {children}
                        </h1>
                      ),
                      h2: ({ children }) => (
                        <h2 className="mb-4 mt-10 text-2xl font-semibold tracking-tight text-foreground">
                          {children}
                        </h2>
                      ),
                      h3: ({ children }) => (
                        <h3 className="mb-3 mt-8 text-xl font-semibold tracking-tight text-foreground">
                          {children}
                        </h3>
                      ),
                      p: ({ children }) => (
                        <p className="mb-5 text-lg leading-relaxed text-muted-foreground">
                          {children}
                        </p>
                      ),
                      ul: ({ children }) => (
                        <ul className="mb-5 list-disc space-y-3 pl-6 text-lg text-muted-foreground">
                          {children}
                        </ul>
                      ),
                      ol: ({ children }) => (
                        <ol className="mb-5 list-decimal space-y-3 pl-6 text-lg text-muted-foreground">
                          {children}
                        </ol>
                      ),
                      li: ({ children }) => (
                        <li className="text-muted-foreground">{children}</li>
                      ),
                      strong: ({ children }) => (
                        <strong className="font-semibold text-foreground">
                          {children}
                        </strong>
                      ),
                      a: ({ children, href }) => (
                        <a
                          href={href}
                          className="text-foreground underline underline-offset-4"
                        >
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {answer || "No recommendation generated."}
                  </ReactMarkdown>
                </article>
              </CardContent>
            </Card>

            {/* Latest Technology Signals - Top 5 */}
            <Card className="card-base">
              <CardHeader>
                <CardTitle className="text-base font-medium">Latest Technology Signals</CardTitle>
              </CardHeader>
              <CardContent>
                {(() => {
                  const deduplicated = renderLatestSignals(signals, query);
                  if (deduplicated.length === 0) {
                    return <p className="text-sm text-muted-foreground">No recent technology signals found.</p>;
                  }

                  return (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {deduplicated.map((article, idx) => {
                        const url = extractUrl(article.signal);
                        const Wrapper = url ? 'a' : 'div';
                        const wrapperProps = url ? { href: url, target: '_blank', rel: 'noopener noreferrer' } : {};

                        return (
                          <Wrapper key={idx} {...wrapperProps} className="block">
                            <div className="border border-neutral-300/90 dark:border-neutral-700/80 rounded-lg p-4 bg-white dark:bg-[#14161A] hover:border-neutral-400 dark:hover:border-neutral-600 transition-colors">
                              <div className="flex items-center justify-between mb-2">
                                <span
                                  className="text-xs font-medium px-2 py-0.5 rounded-full text-white"
                                  style={{ backgroundColor: article.source.color }}
                                >
                                  {article.source.label}
                                </span>
                                {article.date && (
                                  <span className="text-xs text-muted-foreground">
                                    {article.date}
                                  </span>
                                )}
                              </div>
                              <h4 className="font-medium text-sm text-foreground mb-1 line-clamp-2">
                                {article.title}
                              </h4>
                              {article.summary && (
                                <p className="text-xs text-muted-foreground line-clamp-2">
                                  {article.summary}
                                </p>
                              )}
                            </div>
                          </Wrapper>
                        );
                      })}
                    </div>
                  );
                })()}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Hide the old research-heavy sections */}
        {/* {topOpportunity && isOpportunity && <ScorePanel opportunity={topOpportunity} />} */}
        {/* {topOpportunity && isOpportunity && <TopOpportunity opportunity={topOpportunity} />} */}
        {/* Source Intelligence removed - research-heavy content moved to Research Summary */}
        {/* {isOpportunity && <RelatedOpportunities opportunities={opportunities.slice(1)} />} */}

        {showPipelineView && investigationId && (
          <div className="mt-8">
            <PipelineAnalysis investigationId={investigationId} />
          </div>
        )}
      </section>
    </>
  );
}
