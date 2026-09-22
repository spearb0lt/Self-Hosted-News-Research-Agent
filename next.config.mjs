// One deployment serves both halves of this app, and how /api/* reaches the
// Python side depends on where it runs.
//
// On Vercel the platform does it: vercel.json routes /api/* to the function in
// api/, before Next sees the request. Next must not rewrite it as well, or the
// request would be sent to an origin that does not exist there.
//
// Everywhere else, Next and uvicorn are two processes and nothing sits in
// front of them. That is true of `npm run dev`, and equally true of the
// container, which runs `npm run start` next to uvicorn on the same host. Both
// therefore need this rewrite: keying it off development instead left every
// self hosted deployment answering 404 to its own frontend.
//
// Set BACKEND_ORIGIN when uvicorn is not on 127.0.0.1:8000.
//
// `output` is deliberately not set: a static export would drop the rewrite and
// could not sit alongside serverless functions on the same origin.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // Next gives a rewrite 30 seconds and then answers 500 itself. A search
    // that fans out across several backends, fetches the article bodies and
    // then has a model read them routinely takes longer than that, and the
    // reply the browser got said the API server was not answering when the
    // API server was busy answering and did so, at 35 seconds, with a 200.
    //
    // 300 seconds is the same budget the pipeline gives itself on a
    // serverless function, so nothing here waits longer than the far end is
    // ever allowed to take.
    proxyTimeout: 300_000,
  },
  async rewrites() {
    if (process.env.VERCEL) {
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
