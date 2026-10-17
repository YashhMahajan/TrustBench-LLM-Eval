import path from "node:path";
import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  output: "standalone",      // A small self-contained server for the Docker image.
  turbopack: { root: path.resolve(__dirname) },
};

export default config;
