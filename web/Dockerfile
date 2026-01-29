FROM node:20-alpine

WORKDIR /app

COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci

COPY web ./web

WORKDIR /app/web
ENV NODE_ENV=production
RUN npm run build

EXPOSE 3000
CMD ["npm", "start", "--", "-p", "3000"]

