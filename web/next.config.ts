import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;

if (process.env.OASIS_LAN_BUILD !== "1") {
  import('@opennextjs/cloudflare').then(m => m.initOpenNextCloudflareForDev());
}
