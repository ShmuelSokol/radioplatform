import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

// One id per build. Open tabs compare it with /version.json to learn that a
// newer release has been published (see src/lib/appUpdate.ts).
const APP_VERSION = process.env.VERCEL_GIT_COMMIT_SHA || `build-${Date.now()}`

const versionFile = () => ({
  name: 'kbr-version-file',
  apply: 'build' as const,
  closeBundle() {
    const out = path.resolve(__dirname, 'dist')
    if (fs.existsSync(out)) {
      fs.writeFileSync(path.join(out, 'version.json'), JSON.stringify({ version: APP_VERSION }))
    }
  },
})

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  // Set VITE_PROXY_TARGET (e.g. https://api.kbrlive.com) in .env.local to develop
  // against the production backend without running it locally.
  const apiTarget = env.VITE_PROXY_TARGET || 'http://localhost:8000'
  const wsTarget = apiTarget.replace(/^http/, 'ws')

  return {
    plugins: [react(), versionFile()],
    define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            'vendor-react': ['react', 'react-dom', 'react-router-dom'],
            'vendor-query': ['@tanstack/react-query'],
            'vendor-hls': ['hls.js'],
          },
        },
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/api/v1/ws': {
          target: wsTarget,
          ws: true,
          changeOrigin: true,
        },
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
