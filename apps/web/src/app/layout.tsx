import type { Metadata } from "next";
import { Inter, Geist_Mono } from "next/font/google";
import { Nav } from "@/components/nav";
import "./globals.css";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ODE | Opportunity Discovery Engine",
  description: "Discover technologies, opportunities, ecosystems, and emerging signals before everyone else.",
};

const themeScript = `
(function () {
  const theme = localStorage.getItem("ode-theme") || "system";
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = theme === "dark" || (theme === "system" && systemDark);
  document.documentElement.classList.toggle("dark", isDark);
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <div className="pointer-events-none fixed inset-0 z-0 bg-grid print-hidden [mask-image:radial-gradient(ellipse_at_center,black_60%,transparent_100%)]" />
        <Nav />
        <div className="relative z-10 flex-1">
          {children}
        </div>
      </body>
    </html>
  );
}
