FROM node:22-slim
WORKDIR /app

# Install Python3 + pip + build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install required Python packages
RUN pip3 install --break-system-packages --no-cache-dir \
    pandas numpy pyarrow psycopg2-binary python-dotenv requests urllib3

# Copy package files and install
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy backend source code
COPY src ./src
COPY .env ./.env

EXPOSE 3001
CMD ["node", "src/server.cjs"]
