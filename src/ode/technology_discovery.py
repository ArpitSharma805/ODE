"""Technology Discovery data pipeline for powering the Technology Discovery UI.

This module provides the data pipeline that runs periodically (or on-demand)
to produce technology discovery feeds with trend scores, momentum, and ecosystem health.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ode.technology_resolver import TECHNOLOGY_REGISTRY, TechnologyProfile
from ode.db import DEFAULT_DB_PATH
from ode.mcp_client import call_tool
from ode.llm import _ollama_generate

logger = logging.getLogger(__name__)


# Curated seed ecosystem data as fallback when GitHub API fails
SEED_ECOSYSTEM_DATA = {
    "langgraph": {"stars": 18500, "projects": 450, "score": 92, "momentum": "High"},
    "mcp": {"stars": 16000, "projects": 380, "score": 95, "momentum": "High"},
    "react": {"stars": 225000, "projects": 12000, "score": 88, "momentum": "Mature"},
    "kubernetes": {"stars": 110000, "projects": 8500, "score": 85, "momentum": "Mature"},
    "rust": {"stars": 98000, "projects": 6200, "score": 89, "momentum": "High"},
    "llama": {"stars": 55000, "projects": 1800, "score": 91, "momentum": "High"},
    "ollama": {"stars": 92000, "projects": 2100, "score": 93, "momentum": "High"},
    "rag": {"stars": 42000, "projects": 1400, "score": 87, "momentum": "High"},
    "docker": {"stars": 68000, "projects": 9000, "score": 82, "momentum": "Mature"},
    "terraform": {"stars": 41000, "projects": 4200, "score": 80, "momentum": "Mature"},
    "next.js": {"stars": 124000, "projects": 7500, "score": 89, "momentum": "High"},
    "tailwind css": {"stars": 81000, "projects": 5600, "score": 84, "momentum": "Mature"},
    "supabase": {"stars": 72000, "projects": 2800, "score": 88, "momentum": "High"},
    "vercel": {"stars": 35000, "projects": 1900, "score": 81, "momentum": "Mature"},
    "deno": {"stars": 94000, "projects": 1600, "score": 79, "momentum": "Moderate"},
    "bun": {"stars": 74000, "projects": 1200, "score": 86, "momentum": "High"},
    "fastapi": {"stars": 76000, "projects": 3400, "score": 87, "momentum": "High"},
    "crewai": {"stars": 22000, "projects": 600, "score": 90, "momentum": "High"},
    "autogen": {"stars": 34000, "projects": 950, "score": 88, "momentum": "High"},
    "vector database": {"stars": 31000, "projects": 800, "score": 83, "momentum": "Moderate"},
    "htmx": {"stars": 39000, "projects": 1100, "score": 85, "momentum": "High"},
    "nomad": {"stars": 15000, "projects": 750, "score": 75, "momentum": "Mature"},
}

# Curated top projects for key technologies
SEED_TOP_PROJECTS = {
    "autogen": [
        {"name": "autogen", "full_name": "microsoft/autogen", "stars": 34500, "forks": 4800, "url": "https://github.com/microsoft/autogen", "description": "A programming framework for agentic AI.", "language": "Python"},
        {"name": "autogen-studio", "full_name": "microsoft/autogen-studio", "stars": 4200, "forks": 600, "url": "https://github.com/microsoft/autogen-studio", "description": "A visual interface for rapidly prototyping multi-agent workflows.", "language": "TypeScript"},
    ],
    "langgraph": [
        {"name": "langgraph", "full_name": "langchain-ai/langgraph", "stars": 18500, "forks": 2200, "url": "https://github.com/langchain-ai/langgraph", "description": "Build resilient language agents as graphs.", "language": "Python"},
        {"name": "langgraph-js", "full_name": "langchain-ai/langgraphjs", "stars": 2400, "forks": 300, "url": "https://github.com/langchain-ai/langgraphjs", "description": "LangGraph in JavaScript/TypeScript.", "language": "TypeScript"},
        {"name": "langgraph-studio", "full_name": "langchain-ai/langgraph-studio", "stars": 1800, "forks": 200, "url": "https://github.com/langchain-ai/langgraph-studio", "description": "A visual interface for building and debugging LangGraph agents.", "language": "TypeScript"},
    ],
    "mcp": [
        {"name": "servers", "full_name": "modelcontextprotocol/servers", "stars": 16200, "forks": 4800, "url": "https://github.com/modelcontextprotocol/servers", "description": "Model Context Protocol reference server implementations.", "language": "TypeScript"},
        {"name": "python-sdk", "full_name": "modelcontextprotocol/python-sdk", "stars": 3100, "forks": 1200, "url": "https://github.com/modelcontextprotocol/python-sdk", "description": "Official Python SDK for MCP.", "language": "Python"},
        {"name": "typescript-sdk", "full_name": "modelcontextprotocol/typescript-sdk", "stars": 2900, "forks": 950, "url": "https://github.com/modelcontextprotocol/typescript-sdk", "description": "Official TypeScript SDK for Model Context Protocol.", "language": "TypeScript"},
        {"name": "inspector", "full_name": "modelcontextprotocol/inspector", "stars": 850, "forks": 620, "url": "https://github.com/modelcontextprotocol/inspector", "description": "Tool for inspecting and debugging MCP servers.", "language": "TypeScript"},
    ],
    "react": [
        {"name": "react", "full_name": "facebook/react", "stars": 225000, "forks": 45000, "url": "https://github.com/facebook/react", "description": "The library for web and native user interfaces.", "language": "JavaScript"},
        {"name": "next.js", "full_name": "vercel/next.js", "stars": 124000, "forks": 25000, "url": "https://github.com/vercel/next.js", "description": "The React Framework for the Web.", "language": "JavaScript"},
        {"name": "remix", "full_name": "remix-run/remix", "stars": 28000, "forks": 3500, "url": "https://github.com/remix-run/remix", "description": "Build better websites with Remix.", "language": "TypeScript"},
        {"name": "shadcn-ui", "full_name": "shadcn-ui/ui", "stars": 68000, "forks": 4000, "url": "https://github.com/shadcn-ui/ui", "description": "Beautifully designed components built with Radix UI and Tailwind CSS.", "language": "TypeScript"},
    ],
    "ollama": [
        {"name": "ollama", "full_name": "ollama/ollama", "stars": 92000, "forks": 7500, "url": "https://github.com/ollama/ollama", "description": "Get up and running with Llama 3, Mistral, and other large language models.", "language": "Go"},
        {"name": "ollama-python", "full_name": "ollama/ollama-python", "stars": 5400, "forks": 600, "url": "https://github.com/ollama/ollama-python", "description": "Ollama Python library.", "language": "Python"},
        {"name": "ollama-js", "full_name": "ollama/ollama-js", "stars": 1200, "forks": 150, "url": "https://github.com/ollama/ollama-js", "description": "Ollama JavaScript/TypeScript library.", "language": "TypeScript"},
    ],
    "kubernetes": [
        {"name": "kubernetes", "full_name": "kubernetes/kubernetes", "stars": 110000, "forks": 38000, "url": "https://github.com/kubernetes/kubernetes", "description": "Production-Grade Container Scheduling and Management.", "language": "Go"},
        {"name": "helm", "full_name": "helm/helm", "stars": 26000, "forks": 7000, "url": "https://github.com/helm/helm", "description": "The Kubernetes Package Manager.", "language": "Go"},
        {"name": "kustomize", "full_name": "kubernetes-sigs/kustomize", "stars": 11000, "forks": 2500, "url": "https://github.com/kubernetes-sigs/kustomize", "description": "Customization of kubernetes YAML configurations.", "language": "Go"},
        {"name": "istio", "full_name": "istio/istio", "stars": 36000, "forks": 7500, "url": "https://github.com/istio/istio", "description": "Connect, secure, control, and observe services.", "language": "Go"},
    ],
    "rust": [
        {"name": "rust", "full_name": "rust-lang/rust", "stars": 98000, "forks": 12500, "url": "https://github.com/rust-lang/rust", "description": "Empowering everyone to build reliable and efficient software.", "language": "Rust"},
        {"name": "tokio", "full_name": "tokio-rs/tokio", "stars": 26000, "forks": 2400, "url": "https://github.com/tokio-rs/tokio", "description": "A runtime for writing reliable asynchronous applications with Rust.", "language": "Rust"},
        {"name": "actix-web", "full_name": "actix/actix-web", "stars": 18000, "forks": 2100, "url": "https://github.com/actix/actix-web", "description": "A powerful, pragmatic, extremely fast web framework for Rust.", "language": "Rust"},
        {"name": "bevy", "full_name": "bevyengine/bevy", "stars": 33000, "forks": 2000, "url": "https://github.com/bevyengine/bevy", "description": "A refreshingly simple data-driven game engine built in Rust.", "language": "Rust"},
    ],
    "crewai": [
        {"name": "crewAI", "full_name": "crewAIInc/crewAI", "stars": 22000, "forks": 2800, "url": "https://github.com/crewAIInc/crewAI", "description": "Framework for orchestrating role-playing, autonomous AI agents.", "language": "Python"},
        {"name": "crewAI-tools", "full_name": "crewAIInc/crewAI-tools", "stars": 2800, "forks": 400, "url": "https://github.com/crewAIInc/crewAI-tools", "description": "Set of tools for CrewAI agents.", "language": "Python"},
    ],
    "supabase": [
        {"name": "supabase", "full_name": "supabase/supabase", "stars": 72000, "forks": 6500, "url": "https://github.com/supabase/supabase", "description": "The open source Firebase alternative.", "language": "TypeScript"},
        {"name": "auth", "full_name": "supabase/auth", "stars": 8500, "forks": 900, "url": "https://github.com/supabase/auth", "description": "Supabase Auth server.", "language": "TypeScript"},
        {"name": "realtime", "full_name": "supabase/realtime", "stars": 4200, "forks": 500, "url": "https://github.com/supabase/realtime", "description": "Supabase Realtime server.", "language": "TypeScript"},
    ],
    "llama": [
        {"name": "llama", "full_name": "meta-llama/llama", "stars": 55000, "forks": 8500, "url": "https://github.com/meta-llama/llama", "description": "Meta Llama large language model.", "language": "Python"},
        {"name": "llama.cpp", "full_name": "ggerganov/llama.cpp", "stars": 62000, "forks": 9500, "url": "https://github.com/ggerganov/llama.cpp", "description": "Port of Facebook's LLaMA model in C/C++.", "language": "C++"},
    ],
    "rag": [
        {"name": "llama-index", "full_name": "run-llama/llama_index", "stars": 36000, "forks": 5200, "url": "https://github.com/run-llama/llama_index", "description": "Data framework for LLM applications.", "language": "Python"},
        {"name": "chroma", "full_name": "chroma-core/chroma", "stars": 14000, "forks": 1400, "url": "https://github.com/chroma-core/chroma", "description": "The open source embedding database.", "language": "Python"},
        {"name": "qdrant", "full_name": "qdrant/qdrant", "stars": 18000, "forks": 1400, "url": "https://github.com/qdrant/qdrant", "description": "Vector similarity search engine.", "language": "Rust"},
    ],
    "docker": [
        {"name": "cli", "full_name": "docker/cli", "stars": 31000, "forks": 8500, "url": "https://github.com/docker/cli", "description": "Docker CLI.", "language": "Go"},
        {"name": "moby", "full_name": "moby/moby", "stars": 68000, "forks": 19000, "url": "https://github.com/moby/moby", "description": "Moby Project - a collaborative project for the container ecosystem.", "language": "Go"},
        {"name": "compose", "full_name": "docker/compose", "stars": 33000, "forks": 5500, "url": "https://github.com/docker/compose", "description": "Define and run multi-container applications with Docker.", "language": "Python"},
    ],
    "terraform": [
        {"name": "terraform", "full_name": "hashicorp/terraform", "stars": 41000, "forks": 9500, "url": "https://github.com/hashicorp/terraform", "description": "Terraform enables you to safely and predictably create, change, and improve infrastructure.", "language": "Go"},
        {"name": "opentofu", "full_name": "opentofu/opentofu", "stars": 8500, "forks": 600, "url": "https://github.com/opentofu/opentofu", "description": "OpenTF is an open-source version of Terraform.", "language": "Go"},
        {"name": "terragrunt", "full_name": "gruntwork-io/terragrunt", "stars": 7200, "forks": 900, "url": "https://github.com/gruntwork-io/terragrunt", "description": "Terragrunt is a thin wrapper for Terraform.", "language": "Go"},
    ],
    "next.js": [
        {"name": "next.js", "full_name": "vercel/next.js", "stars": 124000, "forks": 25000, "url": "https://github.com/vercel/next.js", "description": "The React Framework for the Web.", "language": "JavaScript"},
        {"name": "create-next-app", "full_name": "vercel/create-next-app", "stars": 62000, "forks": 7500, "url": "https://github.com/vercel/create-next-app", "description": "Create Next.js apps in one command.", "language": "TypeScript"},
    ],
    "tailwind css": [
        {"name": "tailwindcss", "full_name": "tailwindlabs/tailwindcss", "stars": 81000, "forks": 4500, "url": "https://github.com/tailwindlabs/tailwindcss", "description": "A utility-first CSS framework for rapid UI development.", "language": "JavaScript"},
        {"name": "headlessui", "full_name": "tailwindlabs/headlessui", "stars": 48000, "forks": 2800, "url": "https://github.com/tailwindlabs/headlessui", "description": "Unstyled, fully accessible UI components.", "language": "JavaScript"},
    ],
    "vercel": [
        {"name": "next.js", "full_name": "vercel/next.js", "stars": 124000, "forks": 25000, "url": "https://github.com/vercel/next.js", "description": "The React Framework for the Web.", "language": "JavaScript"},
        {"name": "ai", "full_name": "vercel/ai", "stars": 8000, "forks": 1200, "url": "https://github.com/vercel/ai", "description": "AI SDK by Vercel.", "language": "TypeScript"},
        {"name": "turborepo", "full_name": "vercel/turborepo", "stars": 24000, "forks": 1800, "url": "https://github.com/vercel/turborepo", "description": "Turborepo.", "language": "TypeScript"},
    ],
    "deno": [
        {"name": "deno", "full_name": "denoland/deno", "stars": 94000, "forks": 5200, "url": "https://github.com/denoland/deno", "description": "A modern runtime for JavaScript and TypeScript.", "language": "TypeScript"},
        {"name": "fresh", "full_name": "denoland/fresh", "stars": 12000, "forks": 600, "url": "https://github.com/denoland/fresh", "description": "The next generation web framework.", "language": "TypeScript"},
    ],
    "bun": [
        {"name": "bun", "full_name": "oven-sh/bun", "stars": 74000, "forks": 2000, "url": "https://github.com/oven-sh/bun", "description": "Incredibly fast JavaScript runtime, bundler, test runner, and package manager.", "language": "Zig"},
    ],
    "fastapi": [
        {"name": "fastapi", "full_name": "fastapi/fastapi", "stars": 76000, "forks": 12500, "url": "https://github.com/fastapi/fastapi", "description": "FastAPI framework, high performance, easy to learn, fast to code, ready for production.", "language": "Python"},
        {"name": "full-stack-fastapi-template", "full_name": "tiangolo/full-stack-fastapi-template", "stars": 18000, "forks": 4500, "url": "https://github.com/tiangolo/full-stack-fastapi-template", "description": "Full stack FastAPI template.", "language": "Python"},
        {"name": "uvicorn", "full_name": "encode/uvicorn", "stars": 23000, "forks": 1800, "url": "https://github.com/encode/uvicorn", "description": "The lightning-fast ASGI server.", "language": "Python"},
    ],
    "vector database": [
        {"name": "qdrant", "full_name": "qdrant/qdrant", "stars": 18000, "forks": 1400, "url": "https://github.com/qdrant/qdrant", "description": "Vector similarity search engine.", "language": "Rust"},
        {"name": "chroma", "full_name": "chroma-core/chroma", "stars": 14000, "forks": 1400, "url": "https://github.com/chroma-core/chroma", "description": "The open source embedding database.", "language": "Python"},
        {"name": "milvus", "full_name": "milvus-io/milvus", "stars": 28000, "forks": 5200, "url": "https://github.com/milvus-io/milvus", "description": "Vector database built for scalable similarity search and AI applications.", "language": "Go"},
        {"name": "weaviate", "full_name": "weaviate/weaviate", "stars": 12000, "forks": 900, "url": "https://github.com/weaviate/weaviate", "description": "Weaviate.", "language": "Go"},
    ],
    "htmx": [
        {"name": "htmx", "full_name": "bigskysoftware/htmx", "stars": 39000, "forks": 2800, "url": "https://github.com/bigskysoftware/htmx", "description": "High power tools for HTML.", "language": "JavaScript"},
        {"name": "idiomorph", "full_name": "bigskysoftware/idiomorph", "stars": 1200, "forks": 100, "url": "https://github.com/bigskysoftware/idiomorph", "description": "Hypermedia-driven application framework.", "language": "JavaScript"},
    ],
    "nomad": [
        {"name": "nomad", "full_name": "hashicorp/nomad", "stars": 15000, "forks": 3500, "url": "https://github.com/hashicorp/nomad", "description": "Nomad is an easy-to-use, flexible, and performant workload orchestrator.", "language": "Go"},
        {"name": "nomad-driver-podman", "full_name": "hashicorp/nomad-driver-podman", "stars": 500, "forks": 150, "url": "https://github.com/hashicorp/nomad-driver-podman", "description": "Nomad driver for Podman.", "language": "Go"},
    ],
    "next.js": [
        {"name": "next.js", "full_name": "vercel/next.js", "stars": 124000, "forks": 25000, "url": "https://github.com/vercel/next.js", "description": "The React Framework for the Web.", "language": "JavaScript"},
        {"name": "ai", "full_name": "vercel/ai", "stars": 8000, "forks": 1200, "url": "https://github.com/vercel/ai", "description": "AI SDK by Vercel.", "language": "TypeScript"},
        {"name": "commerce", "full_name": "vercel/commerce", "stars": 12000, "forks": 3500, "url": "https://github.com/vercel/commerce", "description": "Next.js Commerce.", "language": "TypeScript"},
    ],
    "tailwind css": [
        {"name": "tailwindcss", "full_name": "tailwindlabs/tailwindcss", "stars": 81000, "forks": 4500, "url": "https://github.com/tailwindlabs/tailwindcss", "description": "A utility-first CSS framework for rapid UI development.", "language": "JavaScript"},
        {"name": "headlessui", "full_name": "tailwindlabs/headlessui", "stars": 48000, "forks": 2800, "url": "https://github.com/tailwindlabs/headlessui", "description": "Unstyled, fully accessible UI components.", "language": "JavaScript"},
        {"name": "heroicons", "full_name": "tailwindlabs/heroicons", "stars": 18000, "forks": 1200, "url": "https://github.com/tailwindlabs/heroicons", "description": "Heroicons.", "language": "TypeScript"},
    ],
    "rag": [
        {"name": "llama_index", "full_name": "run-llama/llama_index", "stars": 36000, "forks": 5200, "url": "https://github.com/run-llama/llama_index", "description": "Data framework for LLM applications.", "language": "Python"},
        {"name": "langchain", "full_name": "langchain-ai/langchain", "stars": 95000, "forks": 15000, "url": "https://github.com/langchain-ai/langchain", "description": "LangChain.", "language": "Python"},
        {"name": "chroma", "full_name": "chroma-core/chroma", "stars": 14000, "forks": 1400, "url": "https://github.com/chroma-core/chroma", "description": "The open source embedding database.", "language": "Python"},
        {"name": "qdrant", "full_name": "qdrant/qdrant", "stars": 18000, "forks": 1400, "url": "https://github.com/qdrant/qdrant", "description": "Vector similarity search engine.", "language": "Rust"},
    ],
}

# Curated project suggestions for key technologies
SEED_PROJECT_SUGGESTIONS = {
    "mcp": [
        {"title": "Enterprise MCP Security & Auth Proxy", "description": "Gatekeeper layer for auditing and sanitizing tool access between LLMs and sensitive SQL/API servers.", "difficulty": "Medium"},
        {"title": "Desktop MCP Server Registry & Manager", "description": "GUI application for one-click installing, testing, and sandboxing local MCP tool servers.", "difficulty": "Low"},
        {"title": "Automated MCP Server Generator from OpenAPI", "description": "CLI tool that instantly converts any Swagger/OpenAPI spec into a compliant MCP server.", "difficulty": "Low"}
    ],
    "langgraph": [
        {"title": "Visual State Machine Debugger for LangGraph", "description": "Time-travel state inspector that replays multi-agent node transitions and checkpoint rollbacks.", "difficulty": "Medium"},
        {"title": "Production Agent Evaluation Suite", "description": "Automated regression tester that benchmarks complex LangGraph workflows against adversarial inputs.", "difficulty": "Medium"},
        {"title": "LangGraph Customer Support Pattern Kit", "description": "Production-ready, battle-tested multi-agent customer support templates with human-in-the-loop escalation.", "difficulty": "Low"}
    ],
    "autogen": [
        {"title": "Multi-Agent Conversation Observability Suite", "description": "Real-time telemetry and token cost tracker for long-running autonomous agent loops.", "difficulty": "Medium"},
        {"title": "Domain-Specific Agent Team Templates", "description": "Pre-configured AutoGen teams optimized for legal compliance, code review, and financial research.", "difficulty": "Low"}
    ],
    "react": [
        {"title": "AI-Powered Component Migration Engine", "description": "Automated codemod tool that converts legacy React classes/hooks to React 19 Server Components.", "difficulty": "Medium"},
        {"title": "Specialized Design System Kit for AI Interfaces", "description": "Production component library tailored for streaming AI chat, artifact panels, and canvas UIs.", "difficulty": "Low"}
    ],
    "ollama": [
        {"title": "Local Model Fleet Orchestrator", "description": "Lightweight cluster manager that distributes Ollama inference jobs across multiple local GPUs/machines.", "difficulty": "Medium"},
        {"title": "Enterprise On-Prem RAG Appliance", "description": "Turnkey private knowledge search powered by Ollama and local vector embeddings.", "difficulty": "Medium"}
    ],
    "kubernetes": [
        {"title": "AI Workload Spot Instance Autoscaler", "description": "Kubernetes operator that dynamically schedules GPU training and inference on spot instances to cut cloud costs.", "difficulty": "High"},
        {"title": "Zero-Config Microservice Developer Platform", "description": "Internal developer portal that abstracts Kubernetes YAML into simple service definitions.", "difficulty": "Medium"}
    ],
    "crewai": [
        {"title": "Agent Performance Analytics Dashboard", "description": "Real-time monitoring and benchmarking tool for multi-agent system performance and token efficiency.", "difficulty": "Medium"},
        {"title": "Industry-Specific Agent Templates", "description": "Pre-built agent teams optimized for healthcare, finance, and legal compliance workflows.", "difficulty": "Low"}
    ],
    "supabase": [
        {"title": "Real-time Data Sync Conflict Resolver", "description": "Intelligent conflict resolution system for multi-user real-time database updates.", "difficulty": "Medium"},
        {"title": "Supabase Edge Function Marketplace", "description": "Curated marketplace of reusable edge functions and serverless integrations.", "difficulty": "Low"}
    ],
    "llama": [
        {"title": "LLaMA Fine-Tuning Pipeline Manager", "description": "No-code interface for fine-tuning LLaMA models on custom datasets with automated evaluation.", "difficulty": "Medium"},
        {"title": "Local LLaMA Deployment Toolkit", "description": "One-click deployment scripts for running LLaMA models on various hardware configurations.", "difficulty": "Low"}
    ],
    "rag": [
        {"title": "Hybrid Search Index Optimizer", "description": "Tool that automatically tunes vector search parameters for optimal RAG retrieval accuracy.", "difficulty": "Medium"},
        {"title": "Document Processing Pipeline Builder", "description": "Visual drag-and-drop interface for building custom document ingestion and chunking workflows.", "difficulty": "Low"}
    ],
    "docker": [
        {"title": "Container Security Policy Manager", "description": "Automated policy enforcement and vulnerability scanning for container images across the lifecycle.", "difficulty": "Medium"},
        {"title": "Multi-Cluster Container Orchestration", "description": "Unified management interface for Docker containers across multiple cloud providers.", "difficulty": "High"}
    ],
    "terraform": [
        {"title": "Infrastructure Drift Detection System", "description": "Automated monitoring and alerting for infrastructure configuration drift from Terraform state.", "difficulty": "Medium"},
        {"title": "Terraform Module Marketplace", "description": "Curated marketplace of verified, production-ready Terraform modules and templates.", "difficulty": "Low"}
    ],
    "next.js": [
        {"title": "Next.js Performance Optimization Suite", "description": "Automated performance analysis and optimization recommendations for Next.js applications.", "difficulty": "Medium"},
        {"title": "Server Component Migration Toolkit", "description": "Tools and guides for migrating React components to Next.js Server Components.", "difficulty": "Low"}
    ],
    "tailwind css": [
        {"title": "Tailwind Component Generator", "description": "AI-powered tool that generates Tailwind CSS components from natural language descriptions.", "difficulty": "Medium"},
        {"title": "Design System Version Manager", "description": "Tool for managing and versioning Tailwind-based design systems across projects.", "difficulty": "Low"}
    ],
    "vercel": [
        {"title": "Vercel Deployment Analytics Platform", "description": "Advanced analytics and insights for Vercel deployments and performance monitoring.", "difficulty": "Medium"},
        {"title": "Multi-Cloud Deployment Manager", "description": "Unified interface for deploying to Vercel and other cloud platforms simultaneously.", "difficulty": "High"}
    ],
    "deno": [
        {"title": "Deno Security Scanner", "description": "Automated security analysis and vulnerability detection for Deno applications.", "difficulty": "Medium"},
        {"title": "Node.js to Deno Migration Toolkit", "description": "Tools and guides for migrating Node.js applications to Deno runtime.", "difficulty": "Medium"}
    ],
    "bun": [
        {"title": "Bun Performance Profiler", "description": "Advanced profiling and optimization tools for Bun applications.", "difficulty": "Medium"},
        {"title": "Package Compatibility Bridge", "description": "Compatibility layer for running Node.js packages in Bun runtime.", "difficulty": "High"}
    ],
    "fastapi": [
        {"title": "API Documentation Generator", "description": "Automated generation of comprehensive API documentation from FastAPI endpoints.", "difficulty": "Low"},
        {"title": "FastAPI Microservices Template", "description": "Production-ready template for building FastAPI microservices with best practices.", "difficulty": "Low"}
    ],
    "vector database": [
        {"title": "Vector Database Performance Tuner", "description": "Automated optimization tool for vector database performance and indexing.", "difficulty": "Medium"},
        {"title": "Multi-Vector Database Manager", "description": "Unified interface for managing queries across multiple vector database providers.", "difficulty": "High"}
    ],
    "htmx": [
        {"title": "HTMX Component Library", "description": "Collection of reusable HTMX components and patterns for rapid development.", "difficulty": "Low"},
        {"title": "Progressive Enhancement Toolkit", "description": "Tools for adding HTMX functionality to existing web applications incrementally.", "difficulty": "Low"}
    ],
    "nomad": [
        {"title": "Nomad Workload Scheduler", "description": "Advanced scheduling algorithms for optimizing Nomad workload placement.", "difficulty": "Medium"},
        {"title": "Multi-Cloud Nomad Manager", "description": "Unified management interface for Nomad across different cloud providers.", "difficulty": "High"}
    ],
}


# Canonical repository mappings for accurate GitHub API lookups
CANONICAL_TECH_REPOSITORIES = {
    "autogen": [
        "microsoft/autogen",
        "microsoft/autogen-studio",
    ],
    "langgraph": [
        "langchain-ai/langgraph",
        "langchain-ai/langgraphjs",
        "langchain-ai/langgraph-studio",
    ],
    "mcp": [
        "modelcontextprotocol/servers",
        "modelcontextprotocol/python-sdk",
        "modelcontextprotocol/typescript-sdk",
        "modelcontextprotocol/inspector",
    ],
    "react": [
        "facebook/react",
        "vercel/next.js",
        "remix-run/remix",
        "shadcn-ui/ui",
    ],
    "ollama": [
        "ollama/ollama",
        "ollama/ollama-python",
        "ollama/ollama-js",
    ],
    "kubernetes": [
        "kubernetes/kubernetes",
        "helm/helm",
        "kubernetes-sigs/kustomize",
        "istio/istio",
    ],
    "rust": [
        "rust-lang/rust",
        "tokio-rs/tokio",
        "actix/actix-web",
        "bevyengine/bevy",
    ],
    "crewai": [
        "crewAIInc/crewAI",
        "crewAIInc/crewAI-tools",
    ],
    "supabase": [
        "supabase/supabase",
        "supabase/auth",
        "supabase/realtime",
    ],
    "llama": [
        "meta-llama/llama",
        "ggerganov/llama.cpp",
    ],
    "rag": [
        "run-llama/llama_index",
        "langchain-ai/langchain",
        "chroma-core/chroma",
        "qdrant/qdrant",
    ],
    "vector database": [
        "qdrant/qdrant",
        "chroma-core/chroma",
        "milvus-io/milvus",
        "weaviate/weaviate",
    ],
    "docker": [
        "docker/cli",
        "moby/moby",
        "docker/compose",
    ],
    "terraform": [
        "hashicorp/terraform",
        "opentofu/opentofu",
        "gruntwork-io/terragrunt",
    ],
    "next.js": [
        "vercel/next.js",
        "vercel/ai",
        "vercel/commerce",
    ],
    "tailwind css": [
        "tailwindlabs/tailwindcss",
        "tailwindlabs/headlessui",
        "tailwindlabs/heroicons",
    ],
    "vercel": [
        "vercel/next.js",
        "vercel/ai",
        "vercel/turborepo",
    ],
    "deno": [
        "denoland/deno",
        "denoland/fresh",
    ],
    "bun": [
        "oven-sh/bun",
    ],
    "fastapi": [
        "fastapi/fastapi",
        "tiangolo/full-stack-fastapi-template",
        "encode/uvicorn",
    ],
    "htmx": [
        "bigskysoftware/htmx",
        "bigskysoftware/idiomorph",
    ],
    "nomad": [
        "hashicorp/nomad",
        "hashicorp/nomad-driver-podman",
    ],
}


def fetch_live_github_repo_stats(full_name: str) -> dict[str, Any] | None:
    """Fetch exact live stars, description, language, and pushed date from GitHub REST API.

    Args:
        full_name: Repository full name in format "owner/repo"

    Returns:
        Dictionary with repo metadata or None if fetch fails
    """
    url = f"https://api.github.com/repos/{full_name}"
    headers = {
        "User-Agent": "ODE-Technology-Discovery/1.0",
        "Accept": "application/vnd.github.v3+json",
    }

    # Use GITHUB_TOKEN if available in environment for higher rate limits
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                return {
                    "name": data.get("name", full_name.split("/")[-1]),
                    "full_name": data.get("full_name", full_name),
                    "stars": int(data.get("stargazers_count") or 0),
                    "forks": int(data.get("forks_count") or data.get("forks") or 0),
                    "url": data.get("html_url", f"https://github.com/{full_name}"),
                    "description": data.get("description", ""),
                    "language": data.get("language", ""),
                    "pushed_at": data.get("pushed_at", ""),
                }
    except Exception as exc:
        logger.warning("Failed to fetch live stats for %s: %s", full_name, exc)
    return None


def _fetch_github_repos_for_tech(tech_name: str) -> list[dict[str, Any]]:
    """Fetch real repositories from GitHub MCP for a technology.

    Args:
        tech_name: Technology name to search for

    Returns:
        List of repository dictionaries with full_name, stars, url, description
    """
    try:
        # Use a clean GitHub search query
        query = f"{tech_name} in:name,description,readme sort:stars"
        result = call_tool("github", "search_repositories", {"query": query, "per_page": 50})
        if result and isinstance(result, dict) and "repositories" in result:
            repos = result["repositories"]
            return [
                {
                    "full_name": repo.get("full_name", ""),
                    "name": repo.get("name", ""),
                    "stars": repo.get("stargazers_count", 0),
                    "url": repo.get("html_url", ""),
                    "description": repo.get("description", ""),
                    "pushed_at": repo.get("pushed_at", ""),
                }
                for repo in repos
            ]
    except Exception as e:
        print(f"GitHub MCP fetch failed for {tech_name}: {e}")
    return []


def _insert_github_signals(conn: sqlite3.Connection, tech_name: str, repos: list[dict[str, Any]]) -> None:
    """Insert GitHub repository signals into the database.

    Args:
        conn: SQLite database connection
        tech_name: Technology name
        repos: List of repository dictionaries
    """
    now = datetime.now(timezone.utc).isoformat()

    # Get or create a source for GitHub
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO sources (name, source_type, trust_tier, status) VALUES (?, ?, ?, ?)",
        ("github_discovery", "github", 3, "Active")
    )
    cur.execute("SELECT source_id FROM sources WHERE name = ?", ("github_discovery",))
    source_id = cur.fetchone()[0]

    # Create an ingestion run
    cur.execute(
        "INSERT INTO ingestion_runs (source_id, start_time, status, signals_created) VALUES (?, ?, ?, ?)",
        (source_id, now, "completed", len(repos))
    )
    ingestion_run_id = cur.lastrowid

    # Insert signals for each repository
    for repo in repos:
        # Star count signal
        cur.execute(
            """
            INSERT INTO signals
            (source_id, ingestion_run_id, source_type, entity, metric, value, unit, timestamp, ingest_date,
             raw_payload, normalized_payload, evidence_quality, confidence, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                ingestion_run_id,
                "github",
                repo["full_name"],
                "github_stars",
                str(repo["stars"]),
                "count",
                now,
                now,
                json.dumps(repo),
                json.dumps(repo),
                0.8,
                0.9,
                json.dumps([tech_name.lower(), "github", "repository"])
            )
        )

        # Repository existence signal
        cur.execute(
            """
            INSERT INTO signals
            (source_id, ingestion_run_id, source_type, entity, metric, value, unit, timestamp, ingest_date,
             raw_payload, normalized_payload, evidence_quality, confidence, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                ingestion_run_id,
                "github",
                repo["full_name"],
                "github_repo_results",
                "1",
                "count",
                now,
                now,
                json.dumps(repo),
                json.dumps(repo),
                0.7,
                0.8,
                json.dumps([tech_name.lower(), "github", "repository"])
            )
        )

    conn.commit()


def fetch_live_technology_metrics(profile: TechnologyProfile, conn: sqlite3.Connection) -> dict[str, Any]:
    """Fetch live metrics for a technology from GitHub, Hacker News, and SQLite.

    Args:
        profile: Technology profile from TECHNOLOGY_REGISTRY
        conn: SQLite database connection

    Returns:
        Dictionary with live metrics including project counts, stars, opportunity counts
    """
    # Initialize metrics with defaults
    metrics = {
        "slug": profile.canonical_name.lower().replace(" ", "-"),
        "name": profile.canonical_name,
        "category": profile.category,
        "description": profile.description,
        "maturity": profile.maturity,
        "domain": profile.domain,
        "trend_score": 50,  # Will be calculated
        "momentum": "Emerging",  # Will be calculated
        "project_count": 0,
        "opportunity_count": 0,
        "total_stars": 0,
        "recent_repos_30d": 0,
        "hn_mentions_30d": 0,
        "top_projects": [],
        "related_technologies": profile.related_technologies or [],
        "project_suggestions": [],
    }

    # Check if we have existing signals for this technology
    github_query = f"%{profile.canonical_name}%"
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE (entity LIKE ? OR tags LIKE ?)
        AND (source_type LIKE '%github%' OR metric LIKE '%github%' OR metric LIKE '%star%' OR metric LIKE '%repo%')
        """,
        (github_query, github_query)
    )
    signal_count = cur.fetchone()[0]

    # If no signals exist, actively fetch from GitHub MCP
    if signal_count == 0:
        print(f"No signals found for {profile.canonical_name}, fetching from GitHub MCP...")
        repos = _fetch_github_repos_for_tech(profile.canonical_name)
        if repos:
            _insert_github_signals(conn, profile.canonical_name, repos)
            print(f"Fetched and inserted {len(repos)} repositories for {profile.canonical_name}")

    # 1. Fetch GitHub project data using signals table
    try:
        # Get GitHub repo signals - select all columns to avoid column count mismatch
        cur.execute(
            """
            SELECT signal_id, source_id, ingestion_run_id, source_type, entity, metric, value, unit,
                   timestamp, ingest_date, raw_payload, normalized_payload, evidence_quality, confidence, tags
            FROM signals
            WHERE (entity LIKE ? OR tags LIKE ?)
            AND (source_type LIKE '%github%' OR metric LIKE '%github%' OR metric LIKE '%star%' OR metric LIKE '%repo%')
            ORDER BY timestamp DESC
            LIMIT 50
            """,
            (github_query, github_query)
        )
        github_signals = cur.fetchall()

        # Extract project count and stars from signals
        unique_repos = set()
        total_stars = 0
        recent_repos_30d = 0
        top_projects = []

        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        for signal in github_signals:
            # Extract columns: signal_id, source_id, ingestion_run_id, source_type, entity, metric, value, unit, timestamp, ingest_date, raw_payload, normalized_payload, evidence_quality, confidence, tags
            entity = signal[4]
            value = signal[6]
            metric = signal[5]
            timestamp = signal[8]

            if entity:
                unique_repos.add(entity)

            # Extract star counts
            if metric and "star" in metric.lower():
                try:
                    stars = int(value) if value else 0
                    total_stars += stars
                except (ValueError, TypeError):
                    pass

            # Count recent repos
            if timestamp and timestamp > thirty_days_ago:
                recent_repos_30d += 1

        # Build top projects list
        for repo in list(unique_repos)[:5]:
            top_projects.append({
                "name": repo.split("/")[-1] if "/" in repo else repo,
                "full_name": repo,
                "stars": 0,  # Would need individual repo queries
                "url": f"https://github.com/{repo}" if not repo.startswith("http") else repo,
                "description": ""
            })

        metrics["project_count"] = len(unique_repos)
        metrics["total_stars"] = total_stars
        metrics["recent_repos_30d"] = recent_repos_30d
        metrics["top_projects"] = top_projects

    except Exception as e:
        print(f"Error fetching GitHub metrics for {profile.canonical_name}: {e}")

    # 2. Fetch Hacker News mentions from signals
    try:
        cur.execute(
            """
            SELECT COUNT(*), SUM(CAST(value AS INTEGER))
            FROM signals
            WHERE (entity LIKE ? OR tags LIKE ?)
            AND (source_type LIKE '%hacker%' OR source_type LIKE '%hn%' OR metric LIKE '%discussion%')
            AND timestamp > ?
            """,
            (github_query, github_query, thirty_days_ago)
        )
        result = cur.fetchone()
        if result:
            metrics["hn_mentions_30d"] = result[0] or 0

    except Exception as e:
        print(f"Error fetching HN metrics for {profile.canonical_name}: {e}")

    # 3. Fetch opportunity count from database
    try:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM opportunities
            WHERE title LIKE ? OR description LIKE ? OR category LIKE ?
            """,
            (github_query, github_query, github_query)
        )
        result = cur.fetchone()
        if result:
            metrics["opportunity_count"] = result[0] or 0

    except Exception as e:
        print(f"Error fetching opportunity count for {profile.canonical_name}: {e}")

    # 4. Apply seed data as fallback if metrics are zero or low
    tech_key = profile.canonical_name.lower()

    # Always apply seed data if metrics are zero or baseline
    total_stars_val = int(metrics.get("total_stars", 0) or 0)  # type: ignore
    project_count_val = int(metrics.get("project_count", 0) or 0)  # type: ignore
    trend_score_val = int(metrics.get("trend_score", 0) or 0)  # type: ignore
    if (total_stars_val == 0 or project_count_val == 0 or trend_score_val <= 35):
        if tech_key in SEED_ECOSYSTEM_DATA:
            seed_data = SEED_ECOSYSTEM_DATA[tech_key]
            metrics["total_stars"] = seed_data["stars"]
            metrics["project_count"] = seed_data["projects"]
            metrics["trend_score"] = seed_data["score"]
            metrics["momentum"] = seed_data["momentum"]
        elif profile.seed_stars > 0:
            metrics["total_stars"] = profile.seed_stars
            metrics["project_count"] = profile.seed_projects
            metrics["trend_score"] = profile.seed_score
            metrics["momentum"] = profile.seed_momentum

    # 5. Apply canonical or seed top projects with live GitHub API fetching
    # Always try to get live data for projects if available
    canonical_repos = CANONICAL_TECH_REPOSITORIES.get(tech_key, [])
    seed_projects = SEED_TOP_PROJECTS.get(tech_key, [])

    # Use canonical repos if available, otherwise fall back to seed projects
    source_repos = canonical_repos if canonical_repos else seed_projects

    if source_repos:
        live_projects = []

        # Try to get cached data from database first (fallback when rate-limited)
        try:
            cur.execute(
                """
                SELECT top_projects
                FROM technology_discovery_metrics
                WHERE slug = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (metrics["slug"],)
            )
            cached_row = cur.fetchone()
            if cached_row and cached_row[0]:
                try:
                    cached_projects = json.loads(cached_row[0])
                    if cached_projects and len(cached_projects) > 0:
                        logger.info("Using cached top projects for %s from database", profile.canonical_name)
                        # Use cached data as baseline, will try to update with live data
                        live_projects = cached_projects.copy()
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception as e:
            logger.debug("No cached data found for %s: %s", profile.canonical_name, e)

        # If no cached data, start fresh
        if not live_projects:
            live_projects = []

        # Track which repos we've already processed (from cache)
        processed_full_names = {p.get("full_name", "") for p in live_projects if p.get("full_name")}

        for repo in source_repos:
            # Handle both string (full_name) and dict (project) formats
            repo_full_name = repo if isinstance(repo, str) else str(repo.get("full_name", ""))
            if repo_full_name and repo_full_name not in processed_full_names:
                # Try to fetch live stats from GitHub API
                live_stats = fetch_live_github_repo_stats(repo_full_name)
                if live_stats:
                    # Ensure forks is an integer
                    forks_value = live_stats.get("forks")
                    live_stats["forks"] = int(forks_value) if forks_value is not None else 0
                    live_projects.append(live_stats)
                    logger.info("Fetched live stats for %s: %d stars, %d forks", repo_full_name, live_stats["stars"], live_stats["forks"])
                    processed_full_names.add(repo_full_name)
                else:
                    # Fall back to seed data if available and live fetch fails
                    seed_project = next((p for p in seed_projects if p.get("full_name") == repo_full_name), None)
                    if seed_project:
                        # Ensure forks is an integer
                        forks_value = seed_project.get("forks")
                        seed_project["forks"] = int(forks_value) if forks_value is not None else 0  # type: ignore
                        live_projects.append(seed_project)
                        logger.warning("Using seed data for %s due to fetch failure", repo_full_name)
                        processed_full_names.add(repo_full_name)

        # Sort projects by live star count (descending)
        live_projects.sort(key=lambda x: x.get("stars", 0), reverse=True)

        # Recalculate total_stars as sum of real stars across repos
        total_live_stars = sum(project.get("stars", 0) for project in live_projects)
        if total_live_stars > 0:
            metrics["total_stars"] = total_live_stars

        # Always use the live-fetched projects
        metrics["top_projects"] = live_projects

    # 6. Apply seed project suggestions
    if tech_key in SEED_PROJECT_SUGGESTIONS:
        metrics["project_suggestions"] = SEED_PROJECT_SUGGESTIONS[tech_key]

    # 7. Calculate dynamic trend score (only if not using seed score)
    if metrics["trend_score"] == 50:
        total_stars_val = int(metrics.get("total_stars", 0) or 0)  # type: ignore
        top_projects = metrics.get("top_projects", [])  # type: ignore
        total_forks_val = sum(int(p.get("forks", 0) or 0) for p in top_projects if isinstance(p, dict))
        project_count_val = int(metrics.get("project_count", 0) or 0)  # type: ignore
        recent_repos_30d_val = int(metrics.get("recent_repos_30d", 0) or 0)  # type: ignore
        hn_mentions_val = int(metrics.get("hn_mentions_30d", 0) or 0)  # type: ignore
        opportunity_count_val = int(metrics.get("opportunity_count", 0) or 0)  # type: ignore

        trend_score = calculate_dynamic_trend_score(
            total_stars=total_stars_val,
            total_forks=total_forks_val,
            project_count=project_count_val,
            recent_repos_30d=recent_repos_30d_val,
            hn_mentions=hn_mentions_val,
            opportunity_count=opportunity_count_val
        )
        metrics["trend_score"] = trend_score

    # 8. Calculate momentum classification (only if not using seed momentum)
    if metrics["momentum"] == "Emerging" and tech_key not in SEED_ECOSYSTEM_DATA:
        trend_score_val = int(metrics.get("trend_score", 0) or 0)  # type: ignore
        metrics["momentum"] = classify_momentum(trend_score_val, profile.maturity)

    return metrics


def calculate_dynamic_trend_score(
    total_stars: int,
    total_forks: int,
    project_count: int,
    recent_repos_30d: int = 0,
    hn_mentions: int = 0,
    opportunity_count: int = 0
) -> int:
    """Calculate dynamic trend score based on real metrics.

    Formula:
    - Base score (15 points): For verified technology with repos
    - Star Magnitude (0-35 points): log10(total_stars) * 7.0
    - Fork Volume (0-20 points): log10(total_forks) * 5.0
    - Project Breadth (0-15 points): log10(project_count) * 5.0
    - Recent Velocity (0-15 points): recent_repos_30d * 3
    - Community & Opportunities (0-15 points): (hn_mentions * 3) + (opportunity_count * 2)
    """
    # Base score for verified technology with repos
    base = 15 if total_stars > 0 or project_count > 0 else 0

    # 1. Star Magnitude (0-35 points) - logarithmic scale up to 100k+ stars
    stars_score = min(35, int(math.log10(max(total_stars, 1)) * 7.0)) if total_stars > 0 else 0

    # 2. Fork Volume / Real Developer Usage (0-20 points)
    forks_score = min(20, int(math.log10(max(total_forks, 1)) * 5.0)) if total_forks > 0 else 0

    # 3. Project / Ecosystem Breadth (0-15 points)
    project_score = min(15, int(math.log10(max(project_count, 1)) * 5.0)) if project_count > 0 else 0

    # 4. Recent Velocity (0-15 points)
    velocity_score = min(15, recent_repos_30d * 3)

    # 5. Community & Opportunities (0-15 points)
    community_score = min(15, (hn_mentions * 3) + (opportunity_count * 2))

    final_score = base + stars_score + forks_score + project_score + velocity_score + community_score
    return int(min(99, max(10, final_score)))


def classify_momentum(trend_score: int, maturity: str) -> str:
    """Classify momentum based on trend score and maturity.

    Returns:
        "High" for scores >= 80
        "Moderate" for scores >= 55
        "Emerging" for scores < 55 (unless mature)
        "Mature" for mature technologies with lower scores
    """
    if trend_score >= 80:
        return "High"
    elif trend_score >= 55:
        return "Moderate"
    elif maturity.lower() == "mature":
        return "Mature"
    else:
        return "Emerging"


def cache_technology_metrics(conn: sqlite3.Connection, metrics: dict[str, Any]) -> None:
    """Cache technology metrics in SQLite database.

    Args:
        conn: SQLite database connection
        metrics: Technology metrics dictionary
    """
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT OR REPLACE INTO technology_discovery_metrics
        (slug, name, category, description, maturity, domain, trend_score, momentum,
         project_count, opportunity_count, total_stars, total_forks, recent_repos_30d, hn_mentions_30d,
         top_projects, last_updated, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["slug"],
            metrics["name"],
            metrics["category"],
            metrics["description"],
            metrics["maturity"],
            metrics["domain"],
            int(metrics["trend_score"]),  # Ensure trend_score is explicitly cast to int
            metrics["momentum"],
            int(metrics["project_count"]),  # Ensure project_count is explicitly cast to int
            int(metrics["opportunity_count"]),  # Ensure opportunity_count is explicitly cast to int
            int(metrics["total_stars"]),  # Ensure total_stars is explicitly cast to int
            int(metrics.get("total_forks", 0)),  # Ensure total_forks is explicitly cast to int
            int(metrics["recent_repos_30d"]),  # Ensure recent_repos_30d is explicitly cast to int
            int(metrics["hn_mentions_30d"]),  # Ensure hn_mentions_30d is explicitly cast to int
            json.dumps(metrics["top_projects"]),
            now,
            now
        )
    )


def get_cached_technology_metrics(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """Get cached technology metrics from SQLite.

    Args:
        conn: SQLite database connection
        slug: Technology slug

    Returns:
        Cached metrics dict if fresh (within 6 hours), None otherwise
    """
    six_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()

    cur = conn.cursor()
    cur.execute(
        """
        SELECT slug, name, category, description, maturity, domain, trend_score, momentum,
               project_count, opportunity_count, total_stars, recent_repos_30d, hn_mentions_30d,
               top_projects, last_updated, updated_at
        FROM technology_discovery_metrics
        WHERE slug = ? AND last_updated > ?
        """,
        (slug, six_hours_ago)
    )

    row = cur.fetchone()
    if not row:
        return None

    columns = ["slug", "name", "category", "description", "maturity", "domain",
               "trend_score", "momentum", "project_count", "opportunity_count",
               "total_stars", "recent_repos_30d", "hn_mentions_30d", "top_projects", "last_updated", "updated_at"]

    metrics = dict(zip(columns, row))
    metrics["top_projects"] = json.loads(metrics["top_projects"]) if metrics["top_projects"] else []

    # Apply seed data for cached metrics
    tech_key = metrics["name"].lower()

    # Apply seed top projects if cached projects are empty
    if not metrics["top_projects"] and tech_key in SEED_TOP_PROJECTS:
        metrics["top_projects"] = SEED_TOP_PROJECTS[tech_key]

    # Apply seed project suggestions
    if tech_key in SEED_PROJECT_SUGGESTIONS:
        metrics["project_suggestions"] = SEED_PROJECT_SUGGESTIONS[tech_key]
    else:
        metrics["project_suggestions"] = []

    # Apply related technologies from registry
    if tech_key in TECHNOLOGY_REGISTRY:
        profile = TECHNOLOGY_REGISTRY[tech_key]
        metrics["related_technologies"] = profile.related_technologies or []
    else:
        metrics["related_technologies"] = []

    return metrics


def get_trending_technologies(conn: sqlite3.Connection, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Get trending technologies with live or cached metrics.

    Args:
        conn: SQLite database connection
        force_refresh: If True, fetch fresh data from external sources

    Returns:
        List of technology dictionaries sorted by trend_score descending
    """
    technologies = []

    # Process each technology in the registry
    for tech_name, profile in TECHNOLOGY_REGISTRY.items():
        slug = profile.canonical_name.lower().replace(" ", "-")
        tech_key = profile.canonical_name.lower()

        # Try to get cached metrics first (unless force_refresh)
        if not force_refresh:
            cached = get_cached_technology_metrics(conn, slug)
            if cached:
                # Apply seed data if cached metrics are zero
                if tech_key in SEED_ECOSYSTEM_DATA and cached["total_stars"] == 0:
                    seed_data = SEED_ECOSYSTEM_DATA[tech_key]
                    cached["total_stars"] = seed_data["stars"]
                    cached["project_count"] = seed_data["projects"]
                    cached["trend_score"] = seed_data["score"]
                    cached["momentum"] = seed_data["momentum"]
                technologies.append(cached)
                continue

        # Fetch live metrics using the updated function with GitHub API integration
        metrics = fetch_live_technology_metrics(profile, conn)

        # Cache the metrics in the database
        cache_technology_metrics(conn, metrics)

        technologies.append(metrics)

    # Sort by trend score descending
    technologies.sort(key=lambda x: x["trend_score"], reverse=True)
    return technologies


@dataclass
class TechnologyDiscoveryEntry:
    """Single technology entry for the discovery feed."""

    technology: str
    category: str
    trend_score: float  # 0-100
    momentum: str  # "accelerating" | "steady" | "decelerating" | "emerging"
    momentum_delta: float  # change in trend score over last period
    signal_count_7d: int
    signal_count_30d: int
    top_signals: list[dict[str, Any]]  # 3-5 most notable recent signals
    projects_being_built: list  # recent GitHub repos
    opportunity_count: int
    top_opportunity: str  # headline
    ecosystem_health: dict  # stars, forks, contributors, issues
    last_updated: datetime


@dataclass
class DiscoveryFeed:
    """Complete discovery feed with categorized technology lists."""

    trending: list[TechnologyDiscoveryEntry]  # sorted by momentum
    established: list[TechnologyDiscoveryEntry]  # sorted by ecosystem size
    emerging: list[TechnologyDiscoveryEntry]  # sorted by growth rate
    updated_at: datetime


class TechnologyDiscoveryPipeline:
    """Pipeline for computing technology discovery feeds."""

    def __init__(self, db_path: str = "ode.db"):
        self.db_path = db_path

    def compute_discovery_feed(self) -> DiscoveryFeed:
        """Compute the complete discovery feed from database data.

        This runs a lightweight analysis across all technologies in the registry
        to compute trend scores, momentum, and ecosystem health metrics.

        Returns:
            DiscoveryFeed with trending, established, and emerging technologies
        """
        conn = sqlite3.connect(self.db_path)
        try:
            entries = []

            # Compute discovery entry for each technology in registry
            for tech_name, profile in TECHNOLOGY_REGISTRY.items():
                entry = self._compute_technology_entry(conn, tech_name, profile)
                if entry:
                    entries.append(entry)

            # Categorize entries
            trending = sorted(
                [e for e in entries if e.momentum in ("accelerating", "emerging")],
                key=lambda x: x.trend_score,
                reverse=True
            )

            established = sorted(
                [e for e in entries if e.momentum == "steady"],
                key=lambda x: x.ecosystem_health.get("stars", 0),
                reverse=True
            )

            emerging = sorted(
                [e for e in entries if e.momentum == "emerging"],
                key=lambda x: x.momentum_delta,
                reverse=True
            )

            return DiscoveryFeed(
                trending=trending,
                established=established,
                emerging=emerging,
                updated_at=datetime.now()
            )
        finally:
            conn.close()

    def _compute_technology_entry(
        self,
        conn: sqlite3.Connection,
        tech_name: str,
        profile: TechnologyProfile
    ) -> TechnologyDiscoveryEntry | None:
        """Compute a single technology discovery entry.

        Args:
            conn: Database connection
            tech_name: Technology name
            profile: Technology profile

        Returns:
            TechnologyDiscoveryEntry or None if insufficient data
        """
        # Get signal counts for different time periods
        signal_count_7d = self._count_signals(conn, tech_name, days=7)
        signal_count_30d = self._count_signals(conn, tech_name, days=30)

        if signal_count_30d == 0:
            return None  # Skip technologies with no data

        # Get top recent signals
        top_signals = self._get_top_signals(conn, tech_name, limit=5)

        # Get recent GitHub projects
        projects_being_built = self._get_recent_projects(conn, tech_name, limit=5)

        # Get opportunity count and top opportunity
        opportunity_count, top_opportunity = self._get_opportunity_data(conn, tech_name)

        # Compute ecosystem health
        ecosystem_health = self._compute_ecosystem_health(conn, tech_name, projects_being_built)

        # Compute trend score (0-100)
        trend_score = self._compute_trend_score(
            signal_count_7d,
            signal_count_30d,
            ecosystem_health
        )

        # Compute momentum and momentum delta
        momentum, momentum_delta = self._compute_momentum(
            signal_count_7d,
            signal_count_30d,
            trend_score
        )

        return TechnologyDiscoveryEntry(
            technology=tech_name,
            category=profile.category,
            trend_score=trend_score,
            momentum=momentum,
            momentum_delta=momentum_delta,
            signal_count_7d=signal_count_7d,
            signal_count_30d=signal_count_30d,
            top_signals=top_signals,
            projects_being_built=projects_being_built,
            opportunity_count=opportunity_count,
            top_opportunity=top_opportunity,
            ecosystem_health=ecosystem_health,
            last_updated=datetime.now()
        )

    def _count_signals(self, conn: sqlite3.Connection, tech_name: str, days: int) -> int:
        """Count signals for a technology within a time period."""
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM signals
            WHERE ingest_date >= ?
            AND (entity LIKE ? OR tags LIKE ?)
            """,
            (cutoff_date, f"%{tech_name}%", f"%{tech_name}%")
        )
        return cur.fetchone()[0] or 0

    def _get_top_signals(self, conn: sqlite3.Connection, tech_name: str, limit: int) -> list[dict[str, Any]]:
        """Get top recent signals for a technology."""
        cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        cur = conn.cursor()
        cur.execute(
            """
            SELECT entity, metric, value, confidence, source_type, timestamp
            FROM signals
            WHERE ingest_date >= ?
            AND (entity LIKE ? OR tags LIKE ?)
            ORDER BY confidence DESC, timestamp DESC
            LIMIT ?
            """,
            (cutoff_date, f"%{tech_name}%", f"%{tech_name}%", limit)
        )

        columns = ["entity", "metric", "value", "confidence", "source_type", "timestamp"]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _get_recent_projects(self, conn: sqlite3.Connection, tech_name: str, limit: int) -> list:
        """Get recent GitHub projects for a technology."""
        # This would query GitHub-specific signals or a projects table
        # For now, return empty list as placeholder
        return []

    def _get_opportunity_data(self, conn: sqlite3.Connection, tech_name: str) -> tuple[int, str]:
        """Get opportunity count and top opportunity for a technology."""
        # This would query an opportunities table
        # For now, return placeholder data
        return 0, "No opportunities identified"

    def _compute_ecosystem_health(
        self,
        conn: sqlite3.Connection,
        tech_name: str,
        projects: list
    ) -> dict[str, int]:
        """Compute ecosystem health metrics for a technology."""
        # Placeholder implementation - would aggregate GitHub metrics
        return {
            "stars": sum(p.get("stars", 0) for p in projects if isinstance(p, dict)),
            "forks": sum(p.get("forks", 0) for p in projects if isinstance(p, dict)),
            "contributors": sum(p.get("contributors", 0) for p in projects if isinstance(p, dict)),
            "issues": sum(p.get("issues", 0) for p in projects if isinstance(p, dict)),
        }

    def _compute_trend_score(
        self,
        signal_count_7d: int,
        signal_count_30d: int,
        ecosystem_health: dict[str, int]
    ) -> float:
        """Compute a trend score (0-100) for a technology.

        Factors:
        - Signal volume (30d)
        - Signal growth rate (7d vs 30d)
        - Ecosystem health (stars, forks, contributors)
        """
        # Signal volume score (0-40 points)
        volume_score = min(signal_count_30d / 100, 1.0) * 40

        # Growth rate score (0-30 points)
        if signal_count_30d > 0:
            growth_rate = signal_count_7d / (signal_count_30d / 4)  # Normalize to 7d vs 30d
            growth_score = min(growth_rate, 2.0) / 2.0 * 30  # Cap at 2x growth
        else:
            growth_score = 0

        # Ecosystem health score (0-30 points)
        stars = ecosystem_health.get("stars", 0)
        forks = ecosystem_health.get("forks", 0)
        contributors = ecosystem_health.get("contributors", 0)

        health_score = min(stars / 10000, 1.0) * 15 + min(forks / 1000, 1.0) * 10 + min(contributors / 100, 1.0) * 5

        total_score = volume_score + growth_score + health_score
        return min(total_score, 100.0)

    def _compute_momentum(
        self,
        signal_count_7d: int,
        signal_count_30d: int,
        trend_score: float
    ) -> tuple[str, float]:
        """Compute momentum category and delta.

        Returns:
            Tuple of (momentum_category, momentum_delta)
        """
        if signal_count_30d == 0:
            return "emerging", 0.0

        # Calculate growth rate
        if signal_count_30d > 0:
            growth_rate = signal_count_7d / (signal_count_30d / 4)  # Normalize to 7d vs 30d
        else:
            growth_rate = 0

        # Determine momentum category
        if growth_rate >= 1.5:
            momentum = "accelerating"
        elif growth_rate >= 0.8:
            momentum = "steady"
        elif growth_rate >= 0.3:
            momentum = "decelerating"
        else:
            momentum = "emerging"

        # Calculate momentum delta (change in trend score)
        # This would compare with previous period in a real implementation
        momentum_delta = growth_rate * 10  # Placeholder calculation

        return momentum, momentum_delta


def get_discovery_feed(db_path: str = "ode.db") -> DiscoveryFeed:
    """Get the current technology discovery feed.

    This is a convenience function that creates a pipeline instance
    and computes the feed.

    Args:
        db_path: Path to the database

    Returns:
        DiscoveryFeed with current technology data
    """
    pipeline = TechnologyDiscoveryPipeline(db_path)
    return pipeline.compute_discovery_feed()


def cache_discovery_feed(feed: DiscoveryFeed, cache_path: str = "discovery_cache.json") -> None:
    """Cache the discovery feed to disk for faster access.

    Args:
        feed: DiscoveryFeed to cache
        cache_path: Path to cache file
    """
    import json

    # Convert dataclass to dict for JSON serialization
    feed_dict = {
        "trending": [
            {
                "technology": entry.technology,
                "category": entry.category,
                "trend_score": entry.trend_score,
                "momentum": entry.momentum,
                "momentum_delta": entry.momentum_delta,
                "signal_count_7d": entry.signal_count_7d,
                "signal_count_30d": entry.signal_count_30d,
                "opportunity_count": entry.opportunity_count,
                "top_opportunity": entry.top_opportunity,
                "ecosystem_health": entry.ecosystem_health,
                "last_updated": entry.last_updated.isoformat(),
            }
            for entry in feed.trending
        ],
        "established": [
            {
                "technology": entry.technology,
                "category": entry.category,
                "trend_score": entry.trend_score,
                "momentum": entry.momentum,
                "momentum_delta": entry.momentum_delta,
                "signal_count_7d": entry.signal_count_7d,
                "signal_count_30d": entry.signal_count_30d,
                "opportunity_count": entry.opportunity_count,
                "top_opportunity": entry.top_opportunity,
                "ecosystem_health": entry.ecosystem_health,
                "last_updated": entry.last_updated.isoformat(),
            }
            for entry in feed.established
        ],
        "emerging": [
            {
                "technology": entry.technology,
                "category": entry.category,
                "trend_score": entry.trend_score,
                "momentum": entry.momentum,
                "momentum_delta": entry.momentum_delta,
                "signal_count_7d": entry.signal_count_7d,
                "signal_count_30d": entry.signal_count_30d,
                "opportunity_count": entry.opportunity_count,
                "top_opportunity": entry.top_opportunity,
                "ecosystem_health": entry.ecosystem_health,
                "last_updated": entry.last_updated.isoformat(),
            }
            for entry in feed.emerging
        ],
        "updated_at": feed.updated_at.isoformat(),
    }

    with open(cache_path, "w") as f:
        json.dump(feed_dict, f, indent=2)


def load_cached_discovery_feed(cache_path: str = "discovery_cache.json") -> DiscoveryFeed | None:
    """Load a cached discovery feed from disk.

    Args:
        cache_path: Path to cache file

    Returns:
        DiscoveryFeed if cache exists and is recent, None otherwise
    """
    import json
    import os

    if not os.path.exists(cache_path):
        return None

    # Check if cache is recent (less than 1 hour old)
    cache_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
    if datetime.now() - cache_time > timedelta(hours=1):
        return None

    try:
        with open(cache_path, "r") as f:
            feed_dict = json.load(f)

        # Reconstruct DiscoveryFeed from dict
        trending = [
            TechnologyDiscoveryEntry(
                technology=entry["technology"],
                category=entry["category"],
                trend_score=entry["trend_score"],
                momentum=entry["momentum"],
                momentum_delta=entry["momentum_delta"],
                signal_count_7d=entry["signal_count_7d"],
                signal_count_30d=entry["signal_count_30d"],
                top_signals=[],
                projects_being_built=[],
                opportunity_count=entry["opportunity_count"],
                top_opportunity=entry["top_opportunity"],
                ecosystem_health=entry["ecosystem_health"],
                last_updated=datetime.fromisoformat(entry["last_updated"]),
            )
            for entry in feed_dict["trending"]
        ]

        established = [
            TechnologyDiscoveryEntry(
                technology=entry["technology"],
                category=entry["category"],
                trend_score=entry["trend_score"],
                momentum=entry["momentum"],
                momentum_delta=entry["momentum_delta"],
                signal_count_7d=entry["signal_count_7d"],
                signal_count_30d=entry["signal_count_30d"],
                top_signals=[],
                projects_being_built=[],
                opportunity_count=entry["opportunity_count"],
                top_opportunity=entry["top_opportunity"],
                ecosystem_health=entry["ecosystem_health"],
                last_updated=datetime.fromisoformat(entry["last_updated"]),
            )
            for entry in feed_dict["established"]
        ]

        emerging = [
            TechnologyDiscoveryEntry(
                technology=entry["technology"],
                category=entry["category"],
                trend_score=entry["trend_score"],
                momentum=entry["momentum"],
                momentum_delta=entry["momentum_delta"],
                signal_count_7d=entry["signal_count_7d"],
                signal_count_30d=entry["signal_count_30d"],
                top_signals=[],
                projects_being_built=[],
                opportunity_count=entry["opportunity_count"],
                top_opportunity=entry["top_opportunity"],
                ecosystem_health=entry["ecosystem_health"],
                last_updated=datetime.fromisoformat(entry["last_updated"]),
            )
            for entry in feed_dict["emerging"]
        ]

        return DiscoveryFeed(
            trending=trending,
            established=established,
            emerging=emerging,
            updated_at=datetime.fromisoformat(feed_dict["updated_at"])
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _generate_project_suggestions(tech_name: str, category: str) -> list[dict[str, str]]:
    """Generate project suggestions for a technology based on its category.

    Args:
        tech_name: The technology name
        category: The technology category

    Returns:
        List of project suggestion dictionaries with title, description, and difficulty
    """
    # Generic project suggestions based on category
    category_suggestions = {
        "AI & Agents": [
            {"title": f"{tech_name} Integration Framework", "description": f"Standardized integration patterns for connecting {tech_name} with existing systems.", "difficulty": "Medium"},
            {"title": f"{tech_name} Performance Monitor", "description": f"Real-time monitoring and optimization tools for {tech_name} deployments.", "difficulty": "Medium"},
            {"title": f"{tech_name} Security Scanner", "description": f"Automated security analysis and vulnerability detection for {tech_name} applications.", "difficulty": "High"},
        ],
        "Frontend": [
            {"title": f"{tech_name} Component Library", "description": f"Pre-built, customizable UI components optimized for {tech_name} applications.", "difficulty": "Low"},
            {"title": f"{tech_name} Migration Toolkit", "description": f"Tools and guides for migrating existing applications to {tech_name}.", "difficulty": "Medium"},
            {"title": f"{tech_name} Performance Optimizer", "description": f"Automated performance analysis and optimization recommendations for {tech_name}.", "difficulty": "Medium"},
        ],
        "Infrastructure": [
            {"title": f"{tech_name} Deployment Manager", "description": f"Unified deployment and scaling interface for {tech_name} across cloud providers.", "difficulty": "High"},
            {"title": f"{tech_name} Configuration Manager", "description": f"Centralized configuration management for {tech_name} infrastructure.", "difficulty": "Medium"},
            {"title": f"{tech_name} Monitoring Dashboard", "description": f"Comprehensive monitoring and alerting system for {tech_name} workloads.", "difficulty": "Low"},
        ],
        "Data & DevTools": [
            {"title": f"{tech_name} CLI Tooling", "description": f"Command-line interface and developer tools for {tech_name} workflows.", "difficulty": "Low"},
            {"title": f"{tech_name} Data Pipeline Builder", "description": f"Visual interface for building data processing pipelines with {tech_name}.", "difficulty": "Medium"},
            {"title": f"{tech_name} Analytics Platform", "description": f"Analytics and insights platform for {tech_name} data and metrics.", "difficulty": "High"},
        ],
    }

    # Default suggestions if category not found
    default_suggestions = [
        {"title": f"{tech_name} Starter Template", "description": f"Production-ready starter template for {tech_name} projects with best practices.", "difficulty": "Low"},
        {"title": f"{tech_name} Extension Marketplace", "description": f"Curated marketplace of extensions and plugins for {tech_name}.", "difficulty": "Medium"},
        {"title": f"{tech_name} Enterprise Suite", "description": f"Enterprise-grade tools and features for {tech_name} in production environments.", "difficulty": "High"},
    ]

    return category_suggestions.get(category, default_suggestions)


def discover_custom_technology(query: str, conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Discover a custom technology on-demand using LLM and GitHub API.

    This function handles the dynamic discovery of technologies that are not
    in the existing registry or database. It generates a profile using LLM,
    fetches live GitHub data, computes metrics, and caches the result.

    Args:
        query: The technology name to discover (e.g., "Polars", "Vite")
        conn: SQLite database connection for caching

    Returns:
        Technology discovery object with live metrics, or None if discovery fails
    """
    # Check if technology already exists in registry or database
    query_lower = query.lower()

    # Check TECHNOLOGY_REGISTRY
    for tech_key, profile in TECHNOLOGY_REGISTRY.items():
        if query_lower in tech_key.lower() or query_lower in profile.canonical_name.lower():
            logger.info("Technology %s found in registry as %s", query, tech_key)
            # Get existing technology from database
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT slug, name, description, category, trend_score, momentum, total_stars, total_forks, top_projects, project_suggestions FROM technology_discovery_metrics WHERE slug = ?",
                    (tech_key,)
                )
                row = cur.fetchone()
                if row:
                    slug, name, description, category, trend_score, momentum, total_stars, total_forks, top_projects_json, project_suggestions_json = row
                    return {
                        "slug": slug,
                        "name": name,
                        "description": description,
                        "category": category,
                        "trend_score": trend_score,
                        "momentum": momentum,
                        "total_stars": total_stars,
                        "total_forks": total_forks,
                        "top_projects": json.loads(top_projects_json) if top_projects_json else [],
                        "project_suggestions": json.loads(project_suggestions_json) if project_suggestions_json else [],
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    }
            except Exception as e:
                logger.warning("Database lookup failed for registry tech %s: %s", tech_key, e)

            # If not in database, trigger a refresh
            all_techs = get_trending_technologies(conn, force_refresh=True)
            for tech in all_techs:
                if tech["slug"] == tech_key:
                    return tech

    # Check database
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT slug, name, description, category, trend_score, momentum, total_stars, total_forks, top_projects, project_suggestions FROM technology_discovery_metrics"
        )
        existing_techs = cur.fetchall()

        for row in existing_techs:
            slug, name, description, category, trend_score, momentum, total_stars, total_forks, top_projects_json, project_suggestions_json = row
            if query_lower in slug.lower() or query_lower in name.lower():
                logger.info("Technology %s found in database as %s", query, slug)
                return {
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "category": category,
                    "trend_score": trend_score,
                    "momentum": momentum,
                    "total_stars": total_stars,
                    "total_forks": total_forks,
                    "top_projects": json.loads(top_projects_json) if top_projects_json else [],
                    "project_suggestions": json.loads(project_suggestions_json) if project_suggestions_json else [],
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
    except Exception as e:
        logger.warning("Database lookup failed for %s: %s", query, e)

    # Technology not found - perform dynamic discovery
    logger.info("Starting dynamic discovery for new technology: %s", query)

    try:
        # Step 1: Generate profile metadata via LLM
        llm_prompt = f"""
        Generate a concise technology profile for "{query}" with the following JSON format:
        {{
            "name": "{query}",
            "description": "One-sentence technically precise description",
            "category": "AI & Agents, Frontend, Infrastructure, or Data & DevTools",
            "maturity": "emerging, growth, or mature",
            "related_technologies": ["tech1", "tech2", "tech3"]
        }}
        """

        llm_response = _ollama_generate(llm_prompt, format="json")
        if not llm_response:
            logger.warning("LLM profile generation failed for %s", query)
            return None

        try:
            profile_data = json.loads(llm_response)
        except json.JSONDecodeError:
            logger.warning("LLM response was not valid JSON for %s", query)
            return None

        # Step 2: Fetch live GitHub repositories
        github_query = f"{query} in:name,description"
        github_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(github_query)}&sort=stars&per_page=50"

        try:
            req = urllib.request.Request(github_url, headers={"Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    github_data = json.loads(response.read().decode())
                    repos = github_data.get("items", [])
                else:
                    logger.warning("GitHub API returned status %d for %s", response.status, query)
                    repos = []
        except Exception as e:
            logger.warning("GitHub API request failed for %s: %s", query, e)
            repos = []

        # Extract all repositories (removed hardcoded limit of 4)
        top_projects = []
        for repo in repos:
            top_projects.append({
                "name": repo.get("name", ""),
                "full_name": repo.get("full_name", ""),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "url": repo.get("html_url", ""),
                "description": repo.get("description", ""),
                "language": repo.get("language", ""),
                "pushed_at": repo.get("pushed_at", ""),
            })

        # Step 3: Compute real metrics
        total_stars = sum(p["stars"] for p in top_projects)
        total_forks = sum(p["forks"] for p in top_projects)
        project_count = len(repos)  # Total count from GitHub

        # Calculate trend score using improved formula
        trend_score = calculate_dynamic_trend_score(
            total_stars=total_stars,
            total_forks=total_forks,
            project_count=project_count,
            recent_repos_30d=0,  # Will be calculated later
            hn_mentions=0,  # Will be calculated later
            opportunity_count=0  # Will be calculated later
        )

        # Determine momentum based on maturity and trend score
        maturity = profile_data.get("maturity", "emerging")
        momentum = classify_momentum(trend_score, maturity)

        # Step 4: Create technology discovery object
        tech_slug = query.lower().replace(" ", "-").replace("/", "-")
        discovery_data = {
            "slug": tech_slug,
            "name": profile_data.get("name", query),
            "description": profile_data.get("description", f"{query} - A technology"),
            "category": profile_data.get("category", "Data & DevTools"),
            "maturity": maturity,
            "domain": "general",
            "trend_score": trend_score,
            "momentum": momentum,
            "project_count": project_count,
            "opportunity_count": 0,  # Will be calculated later
            "total_stars": total_stars,
            "total_forks": total_forks,  # Add total_forks to the discovery data
            "recent_repos_30d": 0,  # Will be calculated later
            "hn_mentions_30d": 0,  # Will be calculated later
            "top_projects": top_projects,
            "project_suggestions": _generate_project_suggestions(query, profile_data.get("category", "Data & DevTools")),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Step 5: Save to SQLite for future queries
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO technology_discovery_metrics
                (slug, name, description, category, maturity, domain, trend_score, momentum,
                 project_count, opportunity_count, total_stars, total_forks, recent_repos_30d, hn_mentions_30d,
                 top_projects, project_suggestions, last_updated, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tech_slug,
                    discovery_data["name"],
                    discovery_data["description"],
                    discovery_data["category"],
                    discovery_data["maturity"],
                    discovery_data["domain"],
                    int(trend_score),  # Ensure trend_score is explicitly cast to int
                    momentum,
                    int(project_count),  # Ensure project_count is explicitly cast to int
                    int(discovery_data["opportunity_count"]),  # Ensure opportunity_count is explicitly cast to int
                    int(total_stars),  # Ensure total_stars is explicitly cast to int
                    int(total_forks),  # Ensure total_forks is explicitly cast to int
                    int(discovery_data["recent_repos_30d"]),  # Ensure recent_repos_30d is explicitly cast to int
                    int(discovery_data["hn_mentions_30d"]),  # Ensure hn_mentions_30d is explicitly cast to int
                    json.dumps(top_projects),
                    json.dumps(discovery_data["project_suggestions"]),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                )
            )
            conn.commit()
            logger.info("Successfully cached new technology %s in database", query)
        except Exception as e:
            logger.warning("Failed to cache technology %s in database: %s", query, e)

        return discovery_data

    except Exception as e:
        logger.error("Dynamic discovery failed for %s: %s", query, e)
        return None
