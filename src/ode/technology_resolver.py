"""Technology Resolver and Registry for technology-specific intelligence.

This module provides the core technology resolution infrastructure:
- TechnologyProfile: Rich data model for technology metadata
- TECHNOLOGY_REGISTRY: Curated registry of technology profiles
- TechnologyResolver: Multi-tier resolution logic (registry → LLM → fallback)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TechnologyProfile:
    """Rich profile for a technology with vocabulary control and search guidance."""

    # Identity
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    category: str = ""
    subcategory: str = ""

    # Semantic Anchoring
    description: str = ""
    parent_ecosystem: str = ""
    programming_languages: list[str] = field(default_factory=list)

    # Vocabulary Control
    core_terms: list[str] = field(default_factory=list)
    related_technologies: list[str] = field(default_factory=list)
    competitor_terms: list[str] = field(default_factory=list)

    # Search Control
    search_expansion: list[str] = field(default_factory=list)
    exclusion_terms: list[str] = field(default_factory=list)

    # Classification
    maturity: str = "emerging"  # emerging | growth | mature | declining
    domain: str = ""            # ai-ml | infrastructure | frontend | devtools | data | security
    signal_weight_hints: dict = field(default_factory=dict)

    # Seed ecosystem data (fallback when GitHub API fails)
    seed_stars: int = 0
    seed_projects: int = 0
    seed_score: int = 50
    seed_momentum: str = "Emerging"


@dataclass
class ResolvedQuery:
    """Result of resolving a user query to technology profiles."""

    query_type: str  # single_tech | comparison | category_exploration | unknown
    primary_profile: Optional[TechnologyProfile] = None
    secondary_profiles: list[TechnologyProfile] = field(default_factory=list)
    category_scope: str = ""


# ============================================================================
# CURATED TECHNOLOGY REGISTRY
# ============================================================================

TECHNOLOGY_REGISTRY: dict[str, TechnologyProfile] = {
    "langgraph": TechnologyProfile(
        canonical_name="LangGraph",
        aliases=["langgraph", "lang graph"],
        category="agent-orchestration-framework",
        subcategory="ai-agents",
        description="A framework for building stateful, multi-actor applications with LLMs, built on top of LangChain",
        parent_ecosystem="LangChain",
        programming_languages=["Python", "TypeScript"],
        core_terms=[
            "agents", "workflows", "orchestration", "state", "stateful execution",
            "graph execution", "multi-agent systems", "memory", "cycles", "edges",
            "nodes", "StateGraph", "checkpointing", "MessageGraph", "Pregel"
        ],
        related_technologies=["LangChain", "CrewAI", "AutoGen", "LlamaIndex", "Semantic Kernel"],
        competitor_terms=["CrewAI", "AutoGen", "Semantic Kernel", "Microsoft Autogen"],
        search_expansion=[
            "langgraph tutorial", "langgraph examples", "multi-agent systems",
            "agent workflows", "stateful agents", "langgraph vs crewai",
            "agent orchestration", "langgraph production"
        ],
        exclusion_terms=[
            "knowledge graph", "graph database", "neo4j", "cypher", "triple store",
            "graph neural network", "GNN", "property graph", "RDF", "GraphQL",
            "graph visualization", "vertex/edge", "graph schema",
            "graph query", "graph traversal", "network graph",
            "e-learning platform", "NLP-driven graph",
        ],
        maturity="growth",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=18500,
        seed_projects=450,
        seed_score=92,
        seed_momentum="High",
    ),

    "mcp": TechnologyProfile(
        canonical_name="MCP",
        aliases=["mcp", "model context protocol"],
        category="llm-integration-protocol",
        subcategory="llm-tooling",
        description="Model Context Protocol - an open standard for connecting AI assistants to data sources and tools",
        parent_ecosystem="Anthropic",
        programming_languages=["TypeScript", "Python", "Go", "Rust"],
        core_terms=[
            "mcp", "servers", "tooling", "governance", "clients", "prompts",
            "tools", "context", "anthropic", "claude", "tool calling",
            "resource access", "protocol specification"
        ],
        related_technologies=["Claude", "Anthropic", "OpenAI", "LangChain", "Function Calling"],
        competitor_terms=["openapi", "grpc", "rest", "graphql"],
        search_expansion=[
            "mcp servers", "mcp tools", "model context protocol tutorial",
            "claude mcp", "mcp implementation", "mcp vs openapi"
        ],
        exclusion_terms=[
            "microsoft certified professional", "master control program",
            "modular conveyor", "multiple chip package", "multi-chip package",
            "machine control panel", "membrane capacitance",
        ],
        maturity="emerging",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=16000,
        seed_projects=380,
        seed_score=95,
        seed_momentum="High",
    ),

    "react": TechnologyProfile(
        canonical_name="React",
        aliases=["react", "reactjs", "react.js"],
        category="frontend-framework",
        subcategory="web-development",
        description="A JavaScript library by Meta for building user interfaces using a component-based, declarative approach with a virtual DOM",
        parent_ecosystem="Meta",
        programming_languages=["JavaScript", "TypeScript", "JSX", "TSX"],
        core_terms=[
            "components", "hooks", "useState", "useEffect", "JSX",
            "virtual DOM", "server components", "React Server Components",
            "Suspense", "concurrent rendering", "React Compiler",
            "Next.js", "Remix", "state management"
        ],
        related_technologies=["Next.js", "Remix", "Vue", "Svelte", "Angular", "Solid"],
        competitor_terms=["Vue", "Svelte", "Angular", "Solid", "Qwik", "Astro"],
        search_expansion=[
            "react 19", "react server components", "react compiler",
            "react vs vue 2025", "next.js app router",
            "react state management", "react performance"
        ],
        exclusion_terms=["chemical reaction", "nuclear reactor", "reactive programming"],
        maturity="mature",
        domain="frontend",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "npm": 1.0},
        seed_stars=225000,
        seed_projects=12000,
        seed_score=88,
        seed_momentum="Mature",
    ),

    "kubernetes": TechnologyProfile(
        canonical_name="Kubernetes",
        aliases=["kubernetes", "k8s", "k8"],
        category="container-orchestration",
        subcategory="infrastructure",
        description="Container orchestration platform for automating deployment, scaling, and management of containerized applications",
        parent_ecosystem="CNCF",
        programming_languages=["Go", "YAML", "Python"],
        core_terms=[
            "containers", "pods", "services", "deployments", "namespaces",
            "helm", "kubectl", "scaling", "load balancing", "ingress",
            "service mesh", "StatefulSet", "DaemonSet", "etcd", "kube-proxy"
        ],
        related_technologies=["Docker", "Nomad", "ECS", "Istio", "Envoy", "Terraform", "ArgoCD"],
        competitor_terms=["Nomad", "Docker Swarm", "ECS", "Cloud Run", "Fly.io"],
        search_expansion=[
            "kubernetes best practices", "kubernetes operator pattern",
            "kubernetes platform engineering", "k8s security",
            "kubernetes cost optimization", "kubernetes alternatives"
        ],
        exclusion_terms=[],
        maturity="mature",
        domain="infrastructure",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "web": 0.8},
        seed_stars=110000,
        seed_projects=8500,
        seed_score=85,
        seed_momentum="Mature",
    ),

    "rust": TechnologyProfile(
        canonical_name="Rust",
        aliases=["rust", "rustlang"],
        category="systems-programming-language",
        subcategory="programming-languages",
        description="Systems programming language focused on safety, speed, and concurrency",
        parent_ecosystem="Mozilla",
        programming_languages=["Rust"],
        core_terms=[
            "memory safety", "ownership", "borrowing", "traits", "cargo",
            "crates", "zero-cost abstractions", "unsafe", "ffi",
            "lifetimes", "pattern matching", "rustc"
        ],
        related_technologies=["C++", "Go", "Zig", "Carbon", "WASM"],
        competitor_terms=["C++", "Go", "Zig", "Carbon"],
        search_expansion=[
            "rust tutorial", "rust vs c++", "rust web development",
            "rust embedded", "rust async", "rust ecosystem"
        ],
        exclusion_terms=["rust the game", "rust belt"],
        maturity="growth",
        domain="devtools",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "crates.io": 1.0},
        seed_stars=98000,
        seed_projects=6200,
        seed_score=89,
        seed_momentum="High",
    ),

    "llama": TechnologyProfile(
        canonical_name="LLaMA",
        aliases=["llama", "llama 2", "llama 3", "llama 3.1"],
        category="llm",
        subcategory="ai-ml",
        description="Large Language Model Meta AI - open-source large language models by Meta",
        parent_ecosystem="Meta",
        programming_languages=["Python", "C++"],
        core_terms=[
            "llm", "large language model", "inference", "quantization",
            "fine-tuning", "transformers", "attention", "tokens",
            "parameters", "open source", "meta ai"
        ],
        related_technologies=["PyTorch", "Hugging Face", "Ollama", "vLLM", "TensorRT"],
        competitor_terms=["GPT", "Claude", "Gemini", "Mistral"],
        search_expansion=[
            "llama tutorial", "llama fine-tuning", "llama deployment",
            "llama vs gpt", "llama quantization", "llama inference"
        ],
        exclusion_terms=["animal", "alpaca", "vicuna"],
        maturity="growth",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=55000,
        seed_projects=1800,
        seed_score=91,
        seed_momentum="High",
    ),

    "ollama": TechnologyProfile(
        canonical_name="Ollama",
        aliases=["ollama"],
        category="llm-inference",
        subcategory="ai-ml",
        description="Tool for running and managing large language models locally",
        parent_ecosystem="Independent",
        programming_languages=["Go", "Python"],
        core_terms=[
            "llm", "local inference", "model management", "quantization",
            "gpu acceleration", "api server", "model library",
            "ollama run", "ollama pull", "modelfile"
        ],
        related_technologies=["LLaMA", "Mistral", "Gemma", "vLLM", "llama.cpp"],
        competitor_terms=["vLLM", "LM Studio", "GPT4All", "LocalAI"],
        search_expansion=[
            "ollama tutorial", "ollama models", "ollama api",
            "ollama vs vllm", "local llm", "ollama docker"
        ],
        exclusion_terms=["alpaca", "llama animal"],
        maturity="growth",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=92000,
        seed_projects=2100,
        seed_score=93,
        seed_momentum="High",
    ),

    "rag": TechnologyProfile(
        canonical_name="RAG",
        aliases=["rag", "retrieval augmented generation"],
        category="llm-architecture",
        subcategory="ai-ml",
        description="Retrieval-Augmented Generation - architecture for combining LLMs with external knowledge retrieval",
        parent_ecosystem="Various",
        programming_languages=["Python", "TypeScript"],
        core_terms=[
            "retrieval", "vector database", "embeddings", "chunking",
            "semantic search", "knowledge base", "context window",
            "hybrid search", "reranking", "document processing"
        ],
        related_technologies=["LangChain", "LlamaIndex", "Pinecone", "Weaviate", "Chroma"],
        competitor_terms=["fine-tuning", "prompt engineering"],
        search_expansion=[
            "rag tutorial", "rag architecture", "vector database",
            "langchain rag", "llamaindex", "rag vs fine-tuning"
        ],
        exclusion_terms=["ragdoll", "rag and bone"],
        maturity="growth",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=42000,
        seed_projects=1400,
        seed_score=87,
        seed_momentum="High",
    ),

    "docker": TechnologyProfile(
        canonical_name="Docker",
        aliases=["docker", "docker.io"],
        category="containerization",
        subcategory="infrastructure",
        description="Platform for developing, shipping, and running applications in containers",
        parent_ecosystem="Docker Inc",
        programming_languages=["Go", "Shell"],
        core_terms=[
            "containers", "images", "dockerfile", "docker compose",
            "registry", "docker hub", "containerization", "microservices",
            "volumes", "networks", "docker swarm"
        ],
        related_technologies=["Kubernetes", "Podman", "containerd", "Buildah"],
        competitor_terms=["Podman", "containerd", "Buildah", "LXC"],
        search_expansion=[
            "docker tutorial", "docker best practices", "docker compose",
            "docker vs podman", "container security", "docker optimization"
        ],
        exclusion_terms=["docker clothing", "docker boots"],
        maturity="mature",
        domain="infrastructure",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "web": 0.8},
    ),

    "terraform": TechnologyProfile(
        canonical_name="Terraform",
        aliases=["terraform", "tf"],
        category="infrastructure-as-code",
        subcategory="infrastructure",
        description="Infrastructure as Code tool for building, changing, and versioning infrastructure safely and efficiently",
        parent_ecosystem="HashiCorp",
        programming_languages=["HCL", "Go"],
        core_terms=[
            "infrastructure as code", "iac", "hcl", "modules",
            "providers", "state", "terraform cloud", "terraform enterprise",
            "provisioning", "declarative configuration", "drift detection"
        ],
        related_technologies=["AWS", "Azure", "GCP", "Kubernetes", "Ansible", "Pulumi"],
        competitor_terms=["Pulumi", "AWS CDK", "Ansible", "CloudFormation"],
        search_expansion=[
            "terraform tutorial", "terraform modules", "terraform vs pulumi",
            "terraform best practices", "terraform state", "terraform cloud"
        ],
        exclusion_terms=[],
        maturity="mature",
        domain="infrastructure",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "web": 0.8},
    ),

    "next.js": TechnologyProfile(
        canonical_name="Next.js",
        aliases=["next.js", "nextjs", "next"],
        category="frontend-framework",
        subcategory="web-development",
        description="React framework for building full-stack web applications with server-side rendering and static site generation",
        parent_ecosystem="Vercel",
        programming_languages=["TypeScript", "JavaScript", "React"],
        core_terms=[
            "react", "server components", "app router", "pages router",
            "ssr", "ssg", "isr", "api routes", "middleware",
            "vercel", "edge runtime", "next.config"
        ],
        related_technologies=["React", "Vercel", "Tailwind", "tRPC", "Prisma"],
        competitor_terms=["Remix", "Gatsby", "Nuxt", "SvelteKit"],
        search_expansion=[
            "next.js tutorial", "next.js app router", "next.js vs remix",
            "next.js server components", "next.js deployment", "next.js performance"
        ],
        exclusion_terms=["next clothing", "next season"],
        maturity="mature",
        domain="frontend",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "npm": 1.0},
    ),

    "tailwind": TechnologyProfile(
        canonical_name="Tailwind CSS",
        aliases=["tailwind", "tailwind css", "tailwindcss"],
        category="css-framework",
        subcategory="frontend",
        description="Utility-first CSS framework for rapidly building custom user interfaces",
        parent_ecosystem="Independent",
        programming_languages=["CSS", "JavaScript", "TypeScript"],
        core_terms=[
            "utility-first", "css framework", "responsive design",
            "dark mode", "tailwind config", "purgecss", "jit",
            "arbitrary values", "variants", "plugins"
        ],
        related_technologies=["React", "Vue", "Next.js", "PostCSS"],
        competitor_terms=["Bootstrap", "Bulma", "Material UI", "Chakra UI"],
        search_expansion=[
            "tailwind tutorial", "tailwind components", "tailwind vs bootstrap",
            "tailwind config", "tailwind plugins", "tailwind responsive"
        ],
        exclusion_terms=["wind energy", "tailwind aircraft"],
        maturity="mature",
        domain="frontend",
        signal_weight_hints={"github": 0.7, "hackernews": 0.6, "npm": 1.0},
    ),

    "supabase": TechnologyProfile(
        canonical_name="Supabase",
        aliases=["supabase"],
        category="backend-as-a-service",
        subcategory="infrastructure",
        description="Open source Firebase alternative with PostgreSQL, authentication, edge functions, and real-time subscriptions",
        parent_ecosystem="Supabase",
        programming_languages=["TypeScript", "PostgreSQL", "Go"],
        core_terms=[
            "postgres", "database", "authentication", "real-time",
            "edge functions", "storage", "firebase alternative",
            "row level security", "api", "studio"
        ],
        related_technologies=["PostgreSQL", "Firebase", "PlanetScale", "Neon"],
        competitor_terms=["Firebase", "PlanetScale", "Neon", "Appwrite"],
        search_expansion=[
            "supabase tutorial", "supabase vs firebase", "supabase auth",
            "supabase real-time", "supabase edge functions", "supabase postgres"
        ],
        exclusion_terms=[],
        maturity="growth",
        domain="infrastructure",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=68000,
        seed_projects=9000,
        seed_score=82,
        seed_momentum="Mature",
    ),

    "vercel": TechnologyProfile(
        canonical_name="Vercel",
        aliases=["vercel", "zeit"],
        category="deployment-platform",
        subcategory="infrastructure",
        description="Cloud platform for frontend developers that enables websites and web services to be deployed to global edge network",
        parent_ecosystem="Vercel",
        programming_languages=["TypeScript", "JavaScript"],
        core_terms=[
            "deployment", "edge network", "serverless", "next.js",
            "preview deployments", "analytics", "edge functions",
            "domains", "git integration", "ci/cd"
        ],
        related_technologies=["Next.js", "React", "GitHub", "GitLab"],
        competitor_terms=["Netlify", "Cloudflare Pages", "AWS Amplify", "Heroku"],
        search_expansion=[
            "vercel tutorial", "vercel vs netlify", "vercel deployment",
            "vercel analytics", "vercel edge functions", "vercel domains"
        ],
        exclusion_terms=[],
        maturity="mature",
        domain="infrastructure",
        signal_weight_hints={"github": 0.6, "hackernews": 0.5, "web": 0.8},
    ),

    "deno": TechnologyProfile(
        canonical_name="Deno",
        aliases=["deno"],
        category="javascript-runtime",
        subcategory="devtools",
        description="Secure JavaScript and TypeScript runtime with built-in tooling and dependency management",
        parent_ecosystem="Deno Land",
        programming_languages=["TypeScript", "JavaScript", "Rust"],
        core_terms=[
            "javascript runtime", "typescript", "security", "permissions",
            "deno deploy", "oak", "fresh", "std", "dependency management",
            "web standards", "v8"
        ],
        related_technologies=["Node.js", "Bun", "Node", "TypeScript"],
        competitor_terms=["Node.js", "Bun", "Node"],
        search_expansion=[
            "deno tutorial", "deno vs node", "deno deploy",
            "deno oak", "deno fresh", "deno security"
        ],
        exclusion_terms=["dino", "dinosaur"],
        maturity="growth",
        domain="devtools",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=35000,
        seed_projects=1900,
        seed_score=81,
        seed_momentum="Mature",
    ),

    "bun": TechnologyProfile(
        canonical_name="Bun",
        aliases=["bun"],
        category="javascript-runtime",
        subcategory="devtools",
        description="Fast JavaScript runtime, bundler, test runner, and package manager all in one",
        parent_ecosystem="Bun",
        programming_languages=["TypeScript", "JavaScript", "Zig"],
        core_terms=[
            "javascript runtime", "bundler", "test runner", "package manager",
            "performance", "zig", "bun server", "bun test",
            "hot reload", "typescript support"
        ],
        related_technologies=["Node.js", "Deno", "Vite", "esbuild"],
        competitor_terms=["Node.js", "Deno", "Vite", "esbuild"],
        search_expansion=[
            "bun tutorial", "bun vs node", "bun performance",
            "bun server", "bun test", "bun bundler"
        ],
        exclusion_terms=["bun hair", "bun pastry"],
        maturity="emerging",
        domain="devtools",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=74000,
        seed_projects=1200,
        seed_score=86,
        seed_momentum="High",
    ),

    "fastapi": TechnologyProfile(
        canonical_name="FastAPI",
        aliases=["fastapi", "fast api"],
        category="web-framework",
        subcategory="backend",
        description="Modern, fast web framework for building APIs with Python 3.7+ based on standard Python type hints",
        parent_ecosystem="Independent",
        programming_languages=["Python"],
        core_terms=[
            "api", "rest", "async", "pydantic", "dependency injection",
            "openapi", "swagger", "websocket", "uvicorn", "gunicorn",
            "validation", "serialization"
        ],
        related_technologies=["Python", "Pydantic", "Uvicorn", "SQLAlchemy", "Django"],
        competitor_terms=["Django", "Flask", "Starlette", "Tornado"],
        search_expansion=[
            "fastapi tutorial", "fastapi vs django", "fastapi async",
            "fastapi pydantic", "fastapi websocket", "fastapi deployment"
        ],
        exclusion_terms=["fast food", "fast car"],
        maturity="growth",
        domain="devtools",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=76000,
        seed_projects=3400,
        seed_score=87,
        seed_momentum="High",
    ),

    "crewai": TechnologyProfile(
        canonical_name="CrewAI",
        aliases=["crewai", "crew ai"],
        category="agent-orchestration-framework",
        subcategory="ai-agents",
        description="Framework for orchestrating role-playing autonomous AI agents",
        parent_ecosystem="Independent",
        programming_languages=["Python"],
        core_terms=[
            "agents", "crew", "tasks", "tools", "multi-agent",
            "role-playing", "autonomous", "orchestration", "collaboration",
            "agent workflow", "crew management"
        ],
        related_technologies=["LangGraph", "AutoGen", "LangChain", "LlamaIndex"],
        competitor_terms=["LangGraph", "AutoGen", "Semantic Kernel"],
        search_expansion=[
            "crewai tutorial", "crewai vs langgraph", "crewai agents",
            "crewai tools", "crewai examples", "multi-agent systems"
        ],
        exclusion_terms=["crew", "staff", "team"],
        maturity="emerging",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=22000,
        seed_projects=600,
        seed_score=90,
        seed_momentum="High",
    ),

    "autogen": TechnologyProfile(
        canonical_name="AutoGen",
        aliases=["autogen", "auto gen", "microsoft autogen"],
        category="agent-orchestration-framework",
        subcategory="ai-agents",
        description="Framework for building AI agent applications where agents can work together to solve tasks",
        parent_ecosystem="Microsoft",
        programming_languages=["Python", "C#"],
        core_terms=[
            "agents", "multi-agent", "conversation", "orchestration",
            "llm", "gpt", "agent workflow", "code execution",
            "human-in-the-loop", "agent collaboration"
        ],
        related_technologies=["LangGraph", "CrewAI", "LangChain", "Semantic Kernel"],
        competitor_terms=["LangGraph", "CrewAI", "Semantic Kernel"],
        search_expansion=[
            "autogen tutorial", "autogen vs langgraph", "autogen agents",
            "microsoft autogen", "autogen examples", "multi-agent systems"
        ],
        exclusion_terms=["auto generation", "code generation"],
        maturity="emerging",
        domain="ai-ml",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=34000,
        seed_projects=950,
        seed_score=88,
        seed_momentum="High",
    ),

    "vector database": TechnologyProfile(
        canonical_name="Vector Database",
        aliases=["vector database", "vector db", "vector store"],
        category="database",
        subcategory="data",
        description="Databases optimized for storing and querying vector embeddings for semantic search and RAG applications",
        parent_ecosystem="Various",
        programming_languages=["Python", "Go", "C++"],
        core_terms=[
            "embeddings", "vectors", "similarity search", "semantic search",
            "approximate nearest neighbor", "ann", "indexing", "dimensionality",
            "cosine similarity", "euclidean distance", "rag"
        ],
        related_technologies=["Pinecone", "Weaviate", "Chroma", "Qdrant", "Milvus"],
        competitor_terms=["traditional database", "sql", "postgresql"],
        search_expansion=[
            "vector database tutorial", "pinecone vs weaviate", "vector search",
            "embeddings database", "rag vector database", "ann index"
        ],
        exclusion_terms=["vector graphics", "vector math"],
        maturity="growth",
        domain="data",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=31000,
        seed_projects=800,
        seed_score=83,
        seed_momentum="Moderate",
    ),

    "htmx": TechnologyProfile(
        canonical_name="htmx",
        aliases=["htmx"],
        category="web-library",
        subcategory="frontend",
        description="Library that allows you to access AJAX, CSS Transitions, WebSockets and Server Sent Events directly in HTML",
        parent_ecosystem="Independent",
        programming_languages=["HTML", "JavaScript", "Python", "Go"],
        core_terms=[
            "html", "ajax", "hypermedia", "rest", "hx-attributes",
            "server-side rendering", "progressive enhancement", "websockets",
            "sse", "hx-get", "hx-post", "hx-trigger"
        ],
        related_technologies=["Alpine.js", "Hotwire", "Unpoly", "Intercooler"],
        competitor_terms=["React", "Vue", "Angular", "SPA frameworks"],
        search_expansion=[
            "htmx tutorial", "htmx vs react", "htmx examples",
            "htmx django", "htmx go", "hypermedia driven"
        ],
        exclusion_terms=["htmlx", "html"],
        maturity="growth",
        domain="frontend",
        signal_weight_hints={"github": 0.8, "hackernews": 0.7, "web": 0.9},
        seed_stars=39000,
        seed_projects=1100,
        seed_score=85,
        seed_momentum="High",
    ),
    "nomad": TechnologyProfile(
        canonical_name="Nomad",
        aliases=["nomad", "hashi nomad", "nomad by hashicorp"],
        category="container-orchestration",
        subcategory="workload-orchestrator",
        description="A flexible workload orchestrator by HashiCorp for deploying and managing containers, VMs, and standalone applications across on-prem and cloud environments",
        parent_ecosystem="HashiCorp",
        programming_languages=["Go"],
        core_terms=[
            "job spec", "task drivers", "allocations", "evaluations", "HCL",
            "Consul", "Vault integration", "bin packing", "multi-region", "federation",
            "consul service discovery", "nomad cluster", "client nodes", "server nodes"
        ],
        related_technologies=["Kubernetes", "Docker Swarm", "ECS", "Consul", "Vault", "Terraform"],
        competitor_terms=["Kubernetes", "Docker Swarm", "ECS", "Cloud Run", "Fly.io"],
        search_expansion=[
            "nomad vs kubernetes", "nomad tutorial", "nomad job specification",
            "hashicorp nomad", "nomad consul integration", "nomad vault",
            "nomad multi-region", "nomad bin packing", "nomad drivers"
        ],
        exclusion_terms=["nomad network", "nomad travel", "nomad list"],
        maturity="mature",
        domain="infrastructure",
        signal_weight_hints={"github": 0.8, "hackernews": 0.6, "web": 0.7},
        seed_stars=15000,
        seed_projects=750,
        seed_score=75,
        seed_momentum="Mature",
    ),
}


class TechnologyResolver:
    """Multi-tier technology resolution with registry lookup, LLM fallback, and category detection."""

    def __init__(self, llm_client=None):
        self.registry = TECHNOLOGY_REGISTRY
        self.llm = llm_client

    def resolve(self, query: str, intent: dict | None = None) -> ResolvedQuery:
        """Resolve a user query to technology profiles using multi-tier strategy."""
        normalized = query.strip().lower()

        # Tier 1: Direct registry lookup
        if normalized in self.registry:
            return ResolvedQuery(
                query_type="single_tech",
                primary_profile=self.registry[normalized],
            )

        # Check if any technology name or alias appears in the query
        for key, profile in self.registry.items():
            # Check canonical name
            if key.lower() in normalized:
                return ResolvedQuery(
                    query_type="single_tech",
                    primary_profile=profile,
                )
            # Check aliases
            for alias in profile.aliases:
                if alias.lower() in normalized:
                    return ResolvedQuery(
                        query_type="single_tech",
                        primary_profile=profile,
                    )

        # Check for comparison queries
        comparison = self.detect_comparison(query)
        if comparison:
            return comparison

        # Check for category queries ("what's trending in AI agents")
        category_match = self.detect_category_query(query)
        if category_match:
            return category_match

        # Tier 2: LLM-assisted resolution
        if self.llm:
            return self.llm_resolve(query, intent)

        # Tier 3: Fallback — use query as-is with minimal profile
        return ResolvedQuery(
            query_type="unknown",
            primary_profile=TechnologyProfile(
                canonical_name=query,
                description="",
                maturity="unknown",
            ),
        )

    def detect_comparison(self, query: str) -> ResolvedQuery | None:
        """Detect comparison queries like 'LangGraph vs CrewAI'."""
        comparison_patterns = [" vs ", " versus ", " compared to ", " or "]
        for pattern in comparison_patterns:
            if pattern in query.lower():
                parts = query.lower().split(pattern)
                if len(parts) == 2:
                    left = self.resolve(parts[0].strip())
                    right = self.resolve(parts[1].strip())
                    # Only return comparison if both sides have profiles
                    if left.primary_profile and right.primary_profile:
                        return ResolvedQuery(
                            query_type="comparison",
                            primary_profile=left.primary_profile,
                            secondary_profiles=[right.primary_profile],
                        )
        return None

    def detect_category_query(self, query: str) -> ResolvedQuery | None:
        """Detect category exploration queries like 'what's trending in AI agents'."""
        category_keywords = {
            "ai agents": "agent-orchestration-framework",
            "frontend": "frontend-framework",
            "infrastructure": "container-orchestration",
            "llm tools": "llm-tooling",
            "browser automation": "browser-automation",
        }
        for keyword, category in category_keywords.items():
            if keyword in query.lower():
                matching = [
                    p for p in self.registry.values()
                    if p.category == category or p.subcategory == category
                ]
                return ResolvedQuery(
                    query_type="category_exploration",
                    primary_profile=matching[0] if matching else None,
                    secondary_profiles=matching[1:] if len(matching) > 1 else [],
                    category_scope=category,
                )
        return None

    def llm_resolve(self, query: str, intent: dict | None = None) -> ResolvedQuery:
        """LLM-assisted resolution for ambiguous queries."""
        # This would call the LLM to identify the technology
        # For now, return a fallback
        return ResolvedQuery(
            query_type="unknown",
            primary_profile=TechnologyProfile(
                canonical_name=query,
                description="",
                maturity="unknown",
            ),
        )


# ==================== Title Normalization Helpers ====================

def _mcp_mentioned(
    query: str | None,
    intent: dict[str, Any] | None,
    entity: str = "",
) -> bool:
    """Return True when the query, intent, or entity explicitly concerns MCP."""
    text_parts: list[str] = []
    if query:
        text_parts.append(query.lower())
    if entity:
        text_parts.append(entity.lower())
    safe_intent = intent if isinstance(intent, dict) else {}
    primary = str(safe_intent.get("primary_technology", "")).strip()
    if primary:
        text_parts.append(primary.lower())
    for topic in safe_intent.get("topics", []) or []:
        text_parts.append(str(topic).lower())
    text = " ".join(text_parts)
    return "mcp" in text or "model context protocol" in text


def strip_forced_mcp_prefix(
    title: str,
    query: str | None,
    intent: dict[str, Any] | None,
    entity: str = "",
) -> str:
    """Remove a leading 'MCP' prefix unless the topic is actually about MCP."""
    if _mcp_mentioned(query, intent, entity):
        return title.strip()
    return re.sub(r"^(?:MCP|Mcp|mcp)\b(?:\s*[-:]+\s*|\s+)", "", title).strip()
