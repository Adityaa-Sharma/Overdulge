/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Repo deploys to https://<user>.github.io/Overdulge/ (project page, no
  // custom domain) — see docs/architecture/SYSTEM.md and deploy.yml.
  base: '/Overdulge/',
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
  },
})
