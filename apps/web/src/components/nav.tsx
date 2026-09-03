"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";

const NAV_LINKS = [
  { label: "Intelligence", href: "/" },
  { label: "Tech News", href: "/tech-news" },
  { label: "Architecture", href: "/architecture" },
];

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");

  const applyTheme = useCallback((next: "light" | "dark" | "system") => {
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = next === "dark" || (next === "system" && systemDark);
    document.documentElement.classList.toggle("dark", isDark);
    localStorage.setItem("ode-theme", next);
  }, []);

  useEffect(() => {
    const saved =
      (localStorage.getItem("ode-theme") as "light" | "dark" | "system") ||
      "system";
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(saved);
    applyTheme(saved);
    const listener = () => {
      const current = localStorage.getItem("ode-theme") as
        | "light"
        | "dark"
        | "system"
        | null;
      if (!current || current === "system") {
        applyTheme("system");
      }
    };
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", listener);
    return () =>
      window
        .matchMedia("(prefers-color-scheme: dark)")
        .removeEventListener("change", listener);
  }, [applyTheme]);

  const cycle = () => {
    const order: ("light" | "dark" | "system")[] = ["light", "dark", "system"];
    const next = order[(order.indexOf(theme) + 1) % order.length];
    setTheme(next);
    applyTheme(next);
  };

  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={cycle}
      className="gap-2 text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white hover:bg-neutral-100 dark:hover:bg-neutral-800"
      aria-label="Toggle theme"
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 w-full h-[72px] border-b border-neutral-200/80 bg-white/70 backdrop-blur-md dark:border-neutral-800/80 dark:bg-[#0B0C0E]/70 print-hidden">
      <div className="mx-auto flex h-full max-w-7xl items-center justify-between px-6">

        {/* Left: Logo */}
        <div className="flex items-center gap-2">
          <Link href="/" className="font-bold text-sm tracking-wider uppercase text-neutral-900 dark:text-white">
            ODE
          </Link>
        </div>

        {/* Center: Navigation Links */}
        <nav className="absolute left-1/2 -translate-x-1/2 flex items-center gap-8 text-sm font-medium">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.label}
              href={link.href}
              className={`transition-colors ${
                pathname === link.href
                  ? "text-neutral-900 dark:text-white font-semibold"
                  : "text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
              }`}
            >
              {link.label}
            </Link>
          ))}
        </nav>

        {/* Right: Theme Toggle */}
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
