import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["192.168.35.21"],
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
