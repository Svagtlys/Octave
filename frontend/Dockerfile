FROM node:22-alpine

WORKDIR /app

# Copy dependency files for layer caching
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Change ownership of app directory to node user
RUN chown -R node:node /app

# Use built-in non-root user
USER node

# Expose port
EXPOSE 5173

# Run Vite dev server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
