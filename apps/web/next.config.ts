import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Disable response compression so proxied SSE streams are not buffered.
  // gzip chunking prevents the browser's EventSource from opening until the
  // entire compressed response is flushed, which breaks real-time updates.
  compress: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
