FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000/api/v1
ARG VITE_REALTIME_E2E_STUB=false
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_REALTIME_E2E_STUB=$VITE_REALTIME_E2E_STUB
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/package.json /app/package-lock.json ./
COPY --from=build --chown=app:app /app/node_modules ./node_modules
COPY --from=build --chown=app:app /app/dist ./dist
USER app
EXPOSE 5173
CMD ["npm", "run", "preview"]

