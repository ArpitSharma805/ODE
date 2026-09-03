"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  RefreshCw,
  Clock,
  TrendingUp,
  TrendingDown,
  Minus,
  ChevronRight,
  ChevronDown,
  FolderGit2,
  Star,
  GitFork,
  ExternalLink,
  Package,
  Search,
  X,
  Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface Technology {
  slug: string;
  name: string;
  category: string;
  description: string;
  trend_score: number;
  momentum: string;
  project_count: number;
  opportunity_count: number;
  total_stars: number;
  recent_repos_30d: number;
  hn_mentions_30d: number;
  top_projects: Array<{
    name: string;
    full_name: string;
    stars: number;
    forks?: number;
    url: string;
    description: string;
    language?: string;
    pushed_at?: string;
  }>;
  related_technologies?: string[];
  project_suggestions?: Array<{
    title: string;
    description: string;
    difficulty: string;
  }>;
  last_updated: string;
}

interface TechRadarData {
  last_updated: string;
  cached: boolean;
  technologies: Technology[];
}

const REFRESH_INTERVAL_MS = 30 * 60 * 1000;
const LOCAL_STORAGE_KEY = "ode-tech-discovery";

function TechCard({ tech, rank, onClick }: { tech: Technology; rank: number; onClick: () => void }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.25 }}
    >
      <Card
        className="h-full cursor-pointer border-neutral-300/90 bg-white/80 backdrop-blur transition-colors hover:border-neutral-400 hover:bg-white dark:border-neutral-700/80 dark:bg-[#14161A]/80 dark:hover:border-neutral-600 dark:hover:bg-[#14161A]"
        onClick={onClick}
      >
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3 mb-2.5">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-100 dark:bg-neutral-800 text-xs font-mono font-semibold text-neutral-500 dark:text-neutral-400">
                {rank}
              </span>
              <h3 className="text-base font-bold text-neutral-900 dark:text-neutral-100 truncate">
                {tech.name}
              </h3>
            </div>

            {/* Unified Score + Momentum Pill */}
            <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium shrink-0 border ${
              tech.momentum === 'High'
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200/80 dark:bg-emerald-950/50 dark:text-emerald-300 dark:border-emerald-800/60'
                : tech.momentum === 'Mature'
                ? 'bg-blue-50 text-blue-700 border-blue-200/80 dark:bg-blue-950/50 dark:text-blue-300 dark:border-blue-800/60'
                : 'bg-amber-50 text-amber-700 border-amber-200/80 dark:bg-amber-950/50 dark:text-amber-300 dark:border-amber-800/60'
            }`}>
              <span className="font-mono font-bold text-neutral-900 dark:text-white">
                {typeof tech.trend_score === 'number' ? tech.trend_score : parseInt(tech.trend_score || '0', 10)}
              </span>
              <span className="opacity-40">/100</span>
              <span className="h-2.5 w-[1px] bg-current opacity-25" />
              <span className="font-semibold">{tech.momentum}</span>
            </div>
          </div>
          <CardDescription className="line-clamp-2 text-sm text-muted-foreground">
            {tech.description}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="flex items-center gap-3 pt-3 border-t border-neutral-100 dark:border-neutral-800/80 text-xs text-neutral-500 dark:text-neutral-400">
            <span className="flex items-center gap-1.5">
              <Package className="h-3.5 w-3.5 text-neutral-400 dark:text-neutral-500" />
              <strong className="font-semibold text-neutral-800 dark:text-neutral-200">
                {tech.project_count >= 1000
                  ? `${(tech.project_count / 1000).toFixed(1)}k`
                  : tech.project_count || 0}
              </strong>{" "}
              projects
            </span>

            <span>·</span>

            <span className="flex items-center gap-1.5">
              <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-500" />
              <strong className="font-semibold text-neutral-800 dark:text-neutral-200">
                {tech.total_stars >= 1000
                  ? `${(tech.total_stars / 1000).toFixed(0)}k`
                  : tech.total_stars || 0}
              </strong>{" "}
              stars
            </span>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

function TechDetail({ tech, onClose, technologies }: { tech: Technology; onClose: () => void; technologies: Technology[] }) {
  const [showAllRepos, setShowAllRepos] = useState(false);
  const INITIAL_REPO_COUNT = 6;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 20 }}
      className="mx-auto max-w-5xl"
    >
      <Button variant="ghost" onClick={onClose} className="mb-4 gap-2 pl-0 text-muted-foreground hover:text-foreground">
        <ChevronRight className="h-4 w-4 rotate-180" /> Back to Technologies
      </Button>

      <Card className="border-neutral-300/90 bg-white dark:border-neutral-700/80 dark:bg-[#14161A]">
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 pb-4 border-b border-neutral-200/60 dark:border-neutral-800/60 mb-6">
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-neutral-900 dark:text-white">
                {tech.name}
              </h1>
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-600 dark:text-neutral-300 max-w-2xl">
                {tech.description}
              </p>
            </div>

            {/* Compact Score & Momentum Container */}
            <div className="flex items-center gap-2.5 shrink-0 self-start">
              {/* Trend Score Metric Box */}
              <div className="flex items-center gap-1.5 rounded-xl border border-neutral-200/90 bg-neutral-50 px-3.5 py-2 dark:border-neutral-800 dark:bg-[#16181D]">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500">
                  Trend Score
                </span>
                <span className="font-mono text-lg font-bold text-neutral-900 dark:text-white">
                  {typeof tech.trend_score === 'number' ? tech.trend_score : parseInt(tech.trend_score || '0', 10)}
                </span>
                <span className="text-[10px] text-neutral-400">/100</span>
              </div>

              {/* Momentum Pill */}
              <div className={`inline-flex items-center gap-1 px-3 py-2 rounded-xl text-xs font-semibold border ${
                tech.momentum === 'High'
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                  : tech.momentum === 'Mature'
                  ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20'
                  : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
              }`}>
                <span>{tech.momentum} Momentum</span>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-8">
          {/* CONNECTED ECOSYSTEM TECHNOLOGIES (Static Badges) */}
          {tech.related_technologies && tech.related_technologies.length > 0 && (
            <div className="space-y-2 pt-1 pb-3 border-b border-neutral-200/60 dark:border-neutral-800/60">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                Connected Technologies & Ecosystem
              </div>
              <div className="flex flex-wrap gap-2">
                {tech.related_technologies.map((relTech: string, idx: number) => (
                  <span
                    key={idx}
                    className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium bg-neutral-100 text-neutral-700 dark:bg-neutral-800/80 dark:text-neutral-300 border border-neutral-200/60 dark:border-neutral-700/60 select-none cursor-default"
                  >
                    <span className="text-neutral-400 font-normal select-none">+</span>
                    <span>{relTech}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <section>
            <div className="space-y-3 pt-2">
              <div className="flex items-center justify-between border-b border-neutral-200/60 pb-2 dark:border-neutral-800/60">
                <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                  Top Repositories
                </span>
                <span className="text-[11px] text-neutral-400">
                  {tech.top_projects && tech.top_projects.length > INITIAL_REPO_COUNT
                    ? `${tech.top_projects.length} total repositories`
                    : 'Sorted by stars'}
                </span>
              </div>

              <div className="grid grid-cols-1 gap-2.5">
                {tech.top_projects && tech.top_projects.length > 0 ? (
                  tech.top_projects
                    .slice(0, showAllRepos ? undefined : INITIAL_REPO_COUNT)
                    .map((proj, idx) => (
                    <a
                      key={idx}
                      href={proj.url || `https://github.com/${proj.full_name}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl border border-neutral-300/90 bg-white dark:border-neutral-700/80 dark:bg-[#14161A] hover:border-neutral-400 dark:hover:border-neutral-600 transition-colors"
                    >
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-neutral-400">#{idx + 1}</span>
                          <span className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 group-hover:text-blue-500 transition-colors truncate">
                            {proj.full_name || proj.name}
                          </span>
                          {proj.language && (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                              {proj.language}
                            </span>
                          )}
                        </div>
                        {proj.description && (
                          <p className="text-xs text-neutral-600 dark:text-neutral-400 line-clamp-1">
                            {proj.description}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0 self-start sm:self-center">
                        {/* Stars Badge with Tooltip */}
                        <div
                          className="group/tooltip relative flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/20 text-xs font-mono font-medium cursor-help"
                        >
                          <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-500" />
                          <span>{(proj.stars || 0) >= 1000 ? `${((proj.stars || 0) / 1000).toFixed(1)}k` : proj.stars || 0}</span>

                          {/* Hover Tooltip */}
                          <div className="pointer-events-none absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover/tooltip:flex flex-col items-center z-50 w-48 p-2 rounded-lg bg-neutral-900 text-[11px] font-sans text-neutral-200 shadow-lg dark:bg-neutral-800 border border-neutral-700">
                            <span className="font-semibold text-white mb-0.5">GitHub Stars</span>
                            <span className="text-center leading-tight text-neutral-400">
                              Developer popularity & bookmarking interest across the community.
                            </span>
                            <div className="w-2 h-2 bg-neutral-900 dark:bg-neutral-800 rotate-45 -mb-3 mt-1 border-r border-b border-neutral-700" />
                          </div>
                        </div>

                        {/* Forks Badge with Tooltip */}
                        <div
                          className="group/tooltip relative flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-500/10 text-blue-700 dark:text-blue-300 border border-blue-500/20 text-xs font-mono font-medium cursor-help"
                        >
                          <GitFork className="h-3.5 w-3.5 text-blue-500" />
                          <span>{(proj.forks || 0) >= 1000 ? `${((proj.forks || 0) / 1000).toFixed(1)}k` : proj.forks || 0}</span>

                          {/* Hover Tooltip */}
                          <div className="pointer-events-none absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover/tooltip:flex flex-col items-center z-50 w-48 p-2 rounded-lg bg-neutral-900 text-[11px] font-sans text-neutral-200 shadow-lg dark:bg-neutral-800 border border-neutral-700">
                            <span className="font-semibold text-white mb-0.5">GitHub Forks</span>
                            <span className="text-center leading-tight text-neutral-400">
                              Active development — developers copying code to build extensions & contribute.
                            </span>
                            <div className="w-2 h-2 bg-neutral-900 dark:bg-neutral-800 rotate-45 -mb-3 mt-1 border-r border-b border-neutral-700" />
                          </div>
                        </div>

                        <ExternalLink className="h-3.5 w-3.5 text-neutral-400 group-hover:text-neutral-600 dark:group-hover:text-neutral-200 transition-colors ml-1" />
                      </div>
                    </a>
                  ))
                ) : (
                  <p className="text-sm text-neutral-500 py-2">No projects discovered yet.</p>
                )}

                {tech.top_projects && tech.top_projects.length > INITIAL_REPO_COUNT && (
                  <button
                    onClick={() => setShowAllRepos(!showAllRepos)}
                    className="mt-4 w-full py-2.5 px-4 rounded-lg border border-neutral-300/90 bg-white dark:border-neutral-700/80 dark:bg-[#14161A] text-sm font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-[#1A1D23] transition-colors flex items-center justify-center gap-2"
                  >
                    {showAllRepos ? (
                      <>
                        <ChevronDown className="h-4 w-4 rotate-180" />
                        Show less ({INITIAL_REPO_COUNT})
                      </>
                    ) : (
                      <>
                        <ChevronDown className="h-4 w-4" />
                        Show all {tech.top_projects.length} repositories
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
          </section>

          {/* SECTION 2: TOP PROJECTS BEING BUILT */}
          {tech.project_suggestions && tech.project_suggestions.length > 0 && (
            <div className="space-y-3 pt-6 border-t border-neutral-200/60 dark:border-neutral-800/60">
              <div className="flex items-center justify-between border-b border-neutral-200/60 pb-2 dark:border-neutral-800/60">
                <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500 dark:text-neutral-400">
                  Top Projects Being Built
                </span>
                <span className="text-[11px] text-neutral-400">Ecosystem tools & applications</span>
              </div>

              <div className="grid grid-cols-1 gap-2.5">
                {tech.project_suggestions.map((idea, idx) => (
                  <div
                    key={idx}
                    className="p-4 rounded-xl border border-neutral-300/90 bg-gradient-to-r from-neutral-50/70 to-white dark:border-neutral-700/80 dark:bg-[#14161A] dark:from-[#14161A] dark:to-[#16181E] space-y-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <h4 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                        <span>{idea.title}</span>
                      </h4>
                      {idea.difficulty && (
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300 border border-neutral-200/60 dark:border-neutral-700/60">
                          {idea.difficulty}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
                      {idea.description}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

function TechSkeleton() {
  return (
    <Card className="h-full border-border bg-card/80">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <Skeleton className="h-8 w-8 rounded-full" />
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-5 w-10" />
        </div>
        <Skeleton className="mt-2 h-10 w-full" />
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex gap-3">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-20" />
        </div>
        <Skeleton className="mt-4 h-4 w-28" />
      </CardContent>
    </Card>
  );
}

export default function TechDiscoveryPage() {
  const [data, setData] = useState<TechRadarData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTech, setSelectedTech] = useState<Technology | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [isDiscovering, setIsDiscovering] = useState(false);
  const hasLoadedData = useRef(false);

  const fetchRadar = useCallback(async (refresh = false) => {
    if (refresh) {
      setLoading(true);
    }
    setError(null);
    try {
      if (!refresh && typeof window !== "undefined") {
        const cached = window.localStorage.getItem(LOCAL_STORAGE_KEY);
        if (cached) {
          try {
            const parsed = JSON.parse(cached) as {
              last_updated: string;
              data: TechRadarData;
            };
            const age = Date.now() - new Date(parsed.last_updated).getTime();
            if (age < REFRESH_INTERVAL_MS) {
              setData(parsed.data);
              setLastUpdated(new Date(parsed.data.last_updated));
              hasLoadedData.current = true;
              return;
            }
          } catch {
            // Ignore stale cache parse errors and fetch fresh data
          }
        }
      }

      const res = await fetch(`/api/discovery${refresh ? "?refresh=true" : ""}`, {
        method: "GET",
      });
      if (!res.ok) {
        throw new Error(`Failed to load Technology Discovery: ${res.status}`);
      }
      const text = await res.text();
      let json: TechRadarData;
      try {
        json = JSON.parse(text);
      } catch (e) {
        throw new Error(`Failed to parse response as JSON: ${text.substring(0, 100)}...`);
      }
      setData(json);
      setLastUpdated(new Date(json.last_updated));
      hasLoadedData.current = true;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(
          LOCAL_STORAGE_KEY,
          JSON.stringify({ last_updated: json.last_updated, data: json })
        );
      }
    } catch (err) {
      if (!hasLoadedData.current) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
      console.error("Technology Discovery fetch failed:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleRefresh = () => fetchRadar(true);

  useEffect(() => {
    if (!hasLoadedData.current) {
      fetchRadar();
    }
    const id = setInterval(() => fetchRadar(), REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchRadar]);

  const sortedTechs = useMemo(() => {
    if (!data) return [];

    let filtered = [...data.technologies];

    // Apply category filter
    if (selectedCategory !== "All") {
      const categoryMap: Record<string, string[]> = {
        "AI & Agents": ["mcp", "langgraph", "autogen", "crewai", "ollama", "llama", "rag"],
        "Frontend": ["react", "next.js", "tailwind css", "deno", "bun", "htmx"],
        "Infrastructure": ["kubernetes", "docker", "terraform", "nomad", "vercel"],
        "Data & DevTools": ["vector database", "supabase", "fastapi", "rust"]
      };

      const categoryTechs = categoryMap[selectedCategory] || [];
      filtered = filtered.filter(tech =>
        categoryTechs.includes(tech.slug) ||
        tech.category === selectedCategory
      );
    }

    // Apply search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(tech =>
        tech.name.toLowerCase().includes(query) ||
        tech.category.toLowerCase().includes(query) ||
        tech.description.toLowerCase().includes(query)
      );
    }

    return filtered.sort((a, b) => b.trend_score - a.trend_score);
  }, [data, selectedCategory, searchQuery]);

  const handleDiscoverNewTech = async () => {
    if (!searchQuery) return;

    setIsDiscovering(true);
    try {
      const res = await fetch(`/api/discovery/search?q=${encodeURIComponent(searchQuery)}`);
      const text = await res.text();
      let result;
      try {
        result = JSON.parse(text);
      } catch (e) {
        throw new Error(`Failed to parse response as JSON: ${text.substring(0, 100)}...`);
      }

      if (result.found && result.technology) {
        // Add the new technology to the data
        if (data) {
          setData({
            ...data,
            technologies: [...data.technologies, result.technology]
          });
        }
        // Select the newly discovered technology
        setSelectedTech(result.technology);
        setSearchQuery("");
      } else {
        console.error("Discovery failed:", result.error);
      }
    } catch (err) {
      console.error("Discovery request failed:", err);
    } finally {
      setIsDiscovering(false);
    }
  };

  return (
    <main className="relative min-h-screen overflow-x-hidden bg-transparent text-foreground">

      <div className="mx-auto max-w-7xl px-6 pt-6 pb-16 space-y-6">
        <header className="header-scrim p-8 border border-neutral-200/50 dark:border-neutral-800/50 shadow-sm">
          <div className="inline-flex items-center rounded-full border border-border bg-card px-3 py-1 text-xs font-medium uppercase tracking-widest text-muted-foreground">
            Technology Discovery
          </div>
          <div>
            <h1 className="text-4xl font-semibold leading-tight tracking-tight text-foreground md:text-5xl">
              Trending Technologies
            </h1>
            <p className="mt-3 max-w-2xl text-lg text-muted-foreground">
              Discover what technologies are becoming important, what people are building, and where opportunities exist.
            </p>
          </div>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="inline-flex items-center gap-2 text-sm text-muted-foreground">
              <Clock className="h-4 w-4" />
              {lastUpdated ? (
                <span>Last updated: {lastUpdated.toLocaleString()}</span>
              ) : (
                <span>Loading...</span>
              )}
            </div>
            <Button
              onClick={handleRefresh}
              disabled={loading}
              className="gap-2"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh now
            </Button>
          </div>
        </header>

        {/* Category Filters: Only visible when search input is empty */}
        {!searchQuery.trim() && (
          <div className="mb-6 flex flex-wrap gap-2">
            {["All", "AI & Agents", "Frontend", "Infrastructure", "Data & DevTools"].map((category) => (
              <button
                key={category}
                onClick={() => setSelectedCategory(category)}
                className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                  selectedCategory === category
                    ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                    : "bg-neutral-100 text-neutral-700 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
                }`}
              >
                {category}
              </button>
            ))}
          </div>
        )}

        {/* Search Input Bar */}
        <div className="mb-6 relative">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
            <input
              type="text"
              placeholder="Search technologies..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-10 py-2.5 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900 dark:focus:ring-neutral-100"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="mb-8 flex items-center gap-3 rounded-2xl border border-destructive/20 bg-destructive/10 p-4 text-destructive">
            {error}
          </div>
        )}

        <AnimatePresence mode="wait">
          {selectedTech ? (
            <TechDetail
              key={selectedTech.name}
              tech={selectedTech}
              onClose={() => setSelectedTech(null)}
              technologies={data?.technologies || []}
            />
          ) : (
            <motion.div
              key="grid"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3"
            >
              {loading && !data
                ? Array.from({ length: 9 }).map((_, idx) => <TechSkeleton key={idx} />)
                : sortedTechs.length === 0 && searchQuery ? (
                  <div className="col-span-full flex flex-col items-center justify-center p-8 md:p-12 rounded-xl border border-neutral-300 bg-white/60 dark:border-neutral-700 dark:bg-[#14161A]/60 backdrop-blur-sm text-center max-w-xl mx-auto my-6 space-y-4">
                    <div>
                      <p className="text-sm font-medium text-neutral-900 dark:text-neutral-100 mb-1">
                        No registered technology matching &ldquo;{searchQuery.trim()}&rdquo;
                      </p>
                      <p className="text-xs text-neutral-500 dark:text-neutral-400">
                        Fetch live ecosystem metrics and repositories directly from GitHub.
                      </p>
                    </div>

                    <button
                      onClick={handleDiscoverNewTech}
                      disabled={isDiscovering}
                      className="inline-flex items-center justify-center px-5 py-2.5 rounded-lg text-xs font-semibold bg-neutral-900 text-white hover:bg-neutral-800 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200 disabled:opacity-50 transition-colors shadow-sm"
                    >
                      {isDiscovering ? (
                        <span className="flex items-center gap-2">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          <span>Discovering &ldquo;{searchQuery.trim()}&rdquo;...</span>
                        </span>
                      ) : (
                        <span>Discover &ldquo;{searchQuery.trim()}&rdquo; Live</span>
                      )}
                    </button>
                  </div>
                ) : sortedTechs.map((tech, idx) => (
                    <TechCard
                      key={tech.name}
                      tech={tech}
                      rank={idx + 1}
                      onClick={() => setSelectedTech(tech)}
                    />
                  ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  );
}
