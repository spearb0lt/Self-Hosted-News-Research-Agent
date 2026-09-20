// One deployment serves both halves of this app: Vercel answers /api/* with
// the Python functions under api/ and everything else with this Next app, so
// the browser only ever calls relative URLs and nothing needs rewriting in
// production.
//
// In development there is no Python runtime in front of Next, so /api/* is
// proxied to a local uvicorn instead. Set BACKEND_ORIGIN if uvicorn is not
// listening on 127.0.0.1:8000.
//
// `output` is deliberately not set: a static export would drop the rewrite and
// could not sit alongside serverless functions on the same origin.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (process.env.NODE_ENV !== "development") {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
