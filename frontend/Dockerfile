FROM node:22-alpine

WORKDIR /app

# Copy dependency files for layer caching
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Expose port
EXPOSE 5173

# Run Vite dev server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
