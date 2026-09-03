/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@fin-bp/ui", "@fin-bp/types"],
  experimental: {
    optimizePackageImports: ["antd", "@ant-design/icons"],
  },
};

module.exports = nextConfig;
