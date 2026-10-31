/**
 * Minimal Vite env typing. Deliberately not `/// <reference types="vite/client" />`
 * — that global augmentation redeclares `import.meta.glob` and conflicts with
 * the declaration in src/OperatorConsole.test.tsx. Only `env` is needed here.
 */
interface ImportMetaEnv {
  readonly DEV: boolean
  readonly PROD: boolean
  readonly MODE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
