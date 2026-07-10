import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const VANILLA_DIR = path.resolve(__dirname, '..')
const VENV_PYTHON = path.resolve(__dirname, '../../../../.venv/bin/python')
const DATA_DIR = path.resolve(__dirname, 'public/data')

function regenerateManifest() {
  return new Promise((resolve, reject) => {
    const child = spawn(
      VENV_PYTHON,
      ['-c', "from optimize import regenerate_manifest; regenerate_manifest('viewer/public/data')"],
      { cwd: VANILLA_DIR }
    )
    let stderr = ''
    child.stderr.on('data', (chunk) => (stderr += chunk))
    child.on('error', reject)
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(stderr || `exit ${code}`))))
  })
}

const SYMBOL_RE = /^[A-Z.]{1,10}$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function validateParams(body) {
  const { symbol, date_start, date_end, buy_amount, fee_pct, interval_minutes, max_buys } = body
  if (typeof symbol !== 'string' || !SYMBOL_RE.test(symbol.toUpperCase())) {
    return 'symbol inválido'
  }
  if (typeof date_start !== 'string' || !DATE_RE.test(date_start)) return 'date_start inválido'
  if (typeof date_end !== 'string' || !DATE_RE.test(date_end)) return 'date_end inválido'
  const amount = Number(buy_amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'buy_amount inválido'
  const fee = Number(fee_pct)
  if (!Number.isFinite(fee) || fee < 0) return 'fee_pct inválido'
  const interval = Number(interval_minutes)
  if (!Number.isInteger(interval) || interval <= 0) return 'interval_minutes inválido'
  const maxBuys = Number(max_buys)
  if (!Number.isInteger(maxBuys) || maxBuys <= 0) return 'max_buys inválido'
  return null
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch (err) {
        reject(err)
      }
    })
    req.on('error', reject)
  })
}

function runOptimizeMiddleware() {
  return {
    name: 'run-optimize-middleware',
    configureServer(server) {
      server.middlewares.use('/api/run-optimize', (req, res, next) => {
        if (req.method !== 'POST') return next()

        readJsonBody(req)
          .then((body) => {
            const error = validateParams(body)
            if (error) {
              res.statusCode = 400
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error }))
              return
            }

            const symbol = body.symbol.toUpperCase()
            const args = [
              'optimize.py',
              '--symbol', symbol,
              '--date-start', body.date_start,
              '--date-end', body.date_end,
              '--buy-amount', String(Number(body.buy_amount)),
              '--fee-pct', String(Number(body.fee_pct)),
              '--intervals', String(Number(body.interval_minutes)),
              '--max-buys', String(Number(body.max_buys)),
              '--export-equity-json',
              '--out-dir', 'viewer/public/data',
            ]

            const child = spawn(VENV_PYTHON, args, { cwd: VANILLA_DIR })
            let stdout = ''
            let stderr = ''
            child.stdout.on('data', (chunk) => (stdout += chunk))
            child.stderr.on('data', (chunk) => (stderr += chunk))
            child.on('error', (err) => {
              res.statusCode = 500
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: `No se pudo iniciar optimize.py: ${err.message}` }))
            })
            child.on('close', (code) => {
              res.setHeader('Content-Type', 'application/json')
              if (code !== 0) {
                const errorLine = stdout.split('\n').reverse().find((line) => line.startsWith('Error:'))
                res.statusCode = 500
                res.end(JSON.stringify({ error: errorLine || stderr || `optimize.py salió con código ${code}`, stdout, stderr }))
                return
              }
              res.statusCode = 200
              res.end(JSON.stringify({ ok: true, stdout }))
            })
          })
          .catch((err) => {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: `Body inválido: ${err.message}` }))
          })
      })
    },
  }
}

function deleteRunMiddleware() {
  return {
    name: 'delete-run-middleware',
    configureServer(server) {
      server.middlewares.use('/api/delete-run', (req, res, next) => {
        if (req.method !== 'POST') return next()

        readJsonBody(req)
          .then(async (body) => {
            res.setHeader('Content-Type', 'application/json')
            const file = body.file
            if (typeof file !== 'string' || !file.endsWith('_equity.json')) {
              res.statusCode = 400
              res.end(JSON.stringify({ error: 'file inválido' }))
              return
            }
            const target = path.resolve(DATA_DIR, file)
            if (target !== path.normalize(target) || !target.startsWith(DATA_DIR + path.sep)) {
              res.statusCode = 400
              res.end(JSON.stringify({ error: 'file fuera de la carpeta de datos' }))
              return
            }

            try {
              await fs.unlink(target)
              await regenerateManifest()
              res.statusCode = 200
              res.end(JSON.stringify({ ok: true }))
            } catch (err) {
              res.statusCode = 500
              res.end(JSON.stringify({ error: err.message }))
            }
          })
          .catch((err) => {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: `Body inválido: ${err.message}` }))
          })
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), runOptimizeMiddleware(), deleteRunMiddleware()],
})
