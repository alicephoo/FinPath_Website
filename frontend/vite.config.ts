import { configDefaults, defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
    // Browser E2E specs are Playwright's; keep them out of Vitest.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
