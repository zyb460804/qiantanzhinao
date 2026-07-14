import js from '@eslint/js'
import reactPlugin from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  js.configs.recommended,
  {
    files: ['src/**/*.{js,jsx}'],
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        window: 'readonly',
        document: 'readonly',
        localStorage: 'readonly',
        console: 'readonly',
        fetch: 'readonly',
        URLSearchParams: 'readonly',
        setTimeout: 'readonly',
        setInterval: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
      },
    },
    rules: {
      // JSX awareness — these rules tell ESLint what's used in JSX
      'react/jsx-uses-react': 'error',
      'react/jsx-uses-vars': 'error',

      // React
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
      'react/jsx-no-target-blank': 'warn',

      // React Hooks
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // General
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
    },
    settings: {
      react: { version: '18.3' },
    },
  },
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
]
