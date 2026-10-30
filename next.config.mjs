/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  experimental: {
    serverComponentsExternalPackages: ['@electric-sql/pglite', 'pdf-lib', 'xlsx'],
  },
};

export default nextConfig;
