# The TrustBench dashboard (Next.js). It talks to the API at TRUSTBENCH_API_URL.
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
CMD ["node", "server.js"]
