"use client"

import { useState, useEffect } from "react"
import { ChevronDown, Activity, Layers, Lightbulb, Target, TrendingUp, FileText, Database } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card"
import { Badge } from "./ui/badge"

interface Signal {
  entity?: string
  value?: string
  source_type?: string
  metric?: string
  evidence_quality?: number
}

interface Theme {
  name?: string
  summary?: string
  signals?: Signal[]
}

interface Problem {
  statement?: string
  summary?: string
}

interface Insight {
  statement?: string
  summary?: string
}

interface Opportunity {
  title?: string
  problem?: string
  score?: number
}

interface PipelineArtifacts {
  raw_signals?: unknown[]
  normalized_signals?: Signal[]
  clusters?: unknown[]
  themes?: Theme[]
  problems?: Problem[]
  insights?: Insight[]
  opportunities?: Opportunity[]
  narrative?: unknown
}

interface PipelineAnalysisProps {
  investigationId: number
}

export function PipelineAnalysis({ investigationId }: PipelineAnalysisProps) {
  const [artifacts, setArtifacts] = useState<PipelineArtifacts | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    raw: false,
    normalized: false,
    clusters: false,
    themes: false,
    problems: false,
    insights: false,
    opportunities: false,
    narrative: false,
  })

  useEffect(() => {
    const fetchArtifacts = async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`/api/investigations/${investigationId}/pipeline`)
        if (!res.ok) {
          throw new Error(`Failed to fetch pipeline artifacts: ${res.statusText}`)
        }
        const data = await res.json()
        setArtifacts(data.pipeline_artifacts || {})
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load pipeline artifacts")
      } finally {
        setLoading(false)
      }
    }

    fetchArtifacts()
  }, [investigationId])

  const toggleSection = (section: string) => {
    setOpenSections(prev => ({ ...prev, [section]: !prev[section] }))
  }

  if (loading) {
    return (
      <Card className="border-border bg-muted/30">
        <CardContent className="p-6">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Activity className="h-4 w-4 animate-spin" />
            Loading pipeline analysis...
          </div>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="border-border bg-destructive/10">
        <CardContent className="p-6">
          <div className="text-sm text-destructive">
            Failed to load pipeline analysis: {error}
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!artifacts) {
    return null
  }

  const rawSignals = artifacts.raw_signals || []
  const normalizedSignals = artifacts.normalized_signals || []
  const clusters = artifacts.clusters || []
  const themes = artifacts.themes || []
  const problems = artifacts.problems || []
  const insights = artifacts.insights || []
  const opportunities = artifacts.opportunities || []
  const narrative = artifacts.narrative

  // Group signals by source type
  const signalsBySource = normalizedSignals.reduce<Record<string, Signal[]>>((acc, signal) => {
    const source = signal.source_type || "unknown"
    if (!acc[source]) acc[source] = []
    acc[source].push(signal)
    return acc
  }, {})

  const renderSection = (
    title: string,
    icon: React.ReactNode,
    count: number,
    sectionKey: string,
    content: React.ReactNode
  ): React.ReactNode => (
    <div className="rounded-lg border border-border bg-muted/30 p-3">
      <button
        type="button"
        onClick={() => toggleSection(sectionKey)}
        className="flex w-full items-center justify-between text-left"
      >
        <div className="flex items-center gap-2">
          {icon}
          <span className="font-medium">{title}</span>
          <Badge variant="secondary">{count}</Badge>
        </div>
        <ChevronDown className={`h-4 w-4 transition-transform ${openSections[sectionKey] ? "rotate-180" : ""}`} />
      </button>
      {openSections[sectionKey] ? content : null}
    </div>
  )

  return (
    <Card className="border-border bg-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <Activity className="h-5 w-5" />
          Pipeline Analysis
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {renderSection(
          "Raw Signals",
          <Database className="h-4 w-4 text-muted-foreground" />,
          rawSignals.length,
          "raw",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-2">
              {rawSignals.slice(0, 10).map((signal, idx) => (
                <div key={idx} className="text-sm text-muted-foreground">
                  {typeof signal === 'string' ? signal : JSON.stringify(signal).slice(0, 200)}
                </div>
              ))}
              {rawSignals.length > 10 && (
                <div className="text-xs text-muted-foreground">
                  +{rawSignals.length - 10} more signals
                </div>
              )}
            </div>
          </div>
        )}

        {renderSection(
          "Normalized Signals",
          <Layers className="h-4 w-4 text-muted-foreground" />,
          normalizedSignals.length,
          "normalized",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            {Object.entries(signalsBySource).map(([source, signals]) => (
              <div key={source} className="mb-4 last:mb-0">
                <div className="mb-2 flex items-center gap-2">
                  <Badge variant="outline">{source}</Badge>
                  <span className="text-sm text-muted-foreground">{signals.length} signals</span>
                </div>
                <div className="space-y-1">
                  {signals.slice(0, 5).map((signal, idx) => (
                    <div key={idx} className="text-sm text-muted-foreground pl-2">
                      • {signal.entity || signal.value || JSON.stringify(signal).slice(0, 100)}
                    </div>
                  ))}
                  {signals.length > 5 && (
                    <div className="text-xs text-muted-foreground pl-2">
                      +{signals.length - 5} more
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {renderSection(
          "Clusters",
          <Layers className="h-4 w-4 text-muted-foreground" />,
          clusters.length,
          "clusters",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-2">
              {clusters.map((cluster, idx) => (
                <div key={idx} className="text-sm text-muted-foreground">
                  {typeof cluster === 'string' ? cluster : JSON.stringify(cluster).slice(0, 200)}
                </div>
              ))}
            </div>
          </div>
        )}

        {renderSection(
          "Themes",
          <Layers className="h-4 w-4 text-muted-foreground" />,
          themes.length,
          "themes",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-3">
              {themes.map((theme, idx) => (
                <div key={idx} className="border-b border-border/30 pb-3 last:border-0 last:pb-0">
                  <div className="font-medium text-foreground">{theme.name || `Theme ${idx + 1}`}</div>
                  {theme.summary && (
                    <div className="mt-1 text-sm text-muted-foreground">{theme.summary}</div>
                  )}
                  {theme.signals && Array.isArray(theme.signals) && (
                    <div className="mt-2 text-xs text-muted-foreground">
                      Derived from {theme.signals.length} signals
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {renderSection(
          "Problems",
          <Target className="h-4 w-4 text-muted-foreground" />,
          problems.length,
          "problems",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-3">
              {problems.map((problem, idx) => (
                <div key={idx} className="border-b border-border/30 pb-3 last:border-0 last:pb-0">
                  <div className="font-medium text-foreground">{problem.statement || `Problem ${idx + 1}`}</div>
                  {problem.summary && (
                    <div className="mt-1 text-sm text-muted-foreground">{problem.summary}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {renderSection(
          "Insights",
          <Lightbulb className="h-4 w-4 text-muted-foreground" />,
          insights.length,
          "insights",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-3">
              {insights.map((insight, idx) => (
                <div key={idx} className="border-b border-border/30 pb-3 last:border-0 last:pb-0">
                  <div className="font-medium text-foreground">{insight.statement || `Insight ${idx + 1}`}</div>
                  {insight.summary && (
                    <div className="mt-1 text-sm text-muted-foreground">{insight.summary}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {renderSection(
          "Opportunities",
          <TrendingUp className="h-4 w-4 text-muted-foreground" />,
          opportunities.length,
          "opportunities",
          <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
            <div className="space-y-3">
              {opportunities.map((opp, idx) => (
                <div key={idx} className="border-b border-border/30 pb-3 last:border-0 last:pb-0">
                  <div className="font-medium text-foreground">{opp.title || `Opportunity ${idx + 1}`}</div>
                  {opp.problem && (
                    <div className="mt-1 text-sm text-muted-foreground">{opp.problem}</div>
                  )}
                  {opp.score !== undefined && (
                    <Badge variant="outline" className="mt-2">Score: {opp.score}</Badge>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {narrative ? (
          renderSection(
            "Narrative Summary",
            <FileText className="h-4 w-4 text-muted-foreground" />,
            1,
            "narrative",
            <div className="mt-2 rounded-lg border border-border bg-muted/20 p-4">
              <div className="text-sm text-muted-foreground whitespace-pre-wrap">
                {typeof narrative === 'string' ? narrative : JSON.stringify(narrative)}
              </div>
            </div>
          )
        ) : null}
      </CardContent>
    </Card>
  )
}
