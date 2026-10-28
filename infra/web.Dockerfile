ARG NODE_IMAGE
ARG CADDY_IMAGE
ARG PLAYWRIGHT_IMAGE
FROM ${NODE_IMAGE} AS tools
WORKDIR /workspace
RUN npm install -g pnpm@11.25.0 --ignore-scripts
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json web/package.json
RUN pnpm install --frozen-lockfile
COPY web/ web/
COPY openspec/ openspec/
COPY docs/ docs/
COPY config/ config/
COPY AGENTS.md README.md ./
COPY .prettierrc.json .prettierignore ./
RUN pnpm --filter @echosim/web build

FROM ${PLAYWRIGHT_IMAGE} AS browser
WORKDIR /workspace
RUN npm install -g pnpm@11.25.0 --ignore-scripts
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json web/package.json
RUN pnpm install --frozen-lockfile
COPY web/ web/
CMD ["pnpm", "--filter", "@echosim/web", "e2e"]

FROM ${CADDY_IMAGE} AS runtime
RUN apk del curl
COPY --from=tools /workspace/web/dist/ /srv/
COPY infra/Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
