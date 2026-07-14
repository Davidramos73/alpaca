import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const STRATEGY_DIR = path.resolve(__dirname, '..')
const VENV_PYTHON = path.resolve(__dirname, '../../../../.venv/bin/python')
const DATA_DIR = path.resolve(__dirname, 'public/data')

function regenerateManifest() {
  return new Promise((resolve, reject) => {
    const child = spawn(
      VENV_PYTHON,
      ['-c', "from optimize import regenerate_manifest; regenerate_manifest('viewer/public/data')"],
      { cwd: STRATEGY_DIR }
    )
    let stderr = ''
    child.stderr.on('data', (chunk) => (stderr += chunk))
    child.on('error', reject)
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(stderr || `exit ${code}`))))
  })
}

const SYMBOL_RE = /^[A-Z.]{1,10}$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TRAIL_PCTS_RE = /^\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*$/

function validateParams(body) {
  const { symbol, date_start, date_end, buy_amount, max_buys, fee_pct, trail_buy_pcts, trail_sell_pcts } = body
  if (typeof symbol !== 'string' || !SYMBOL_RE.test(symbol.toUpperCase())) {
    return 'symbol inválido'
  }
  if (typeof date_start !== 'string' || !DATE_RE.test(date_start)) return 'date_start inválido'
  if (typeof date_end !== 'string' || !DATE_RE.test(date_end)) return 'date_end inválido'
  const amount = Number(buy_amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'buy_amount inválido'
  const maxBuys = Number(max_buys)
  if (!Number.isInteger(maxBuys) || maxBuys <= 0) return 'max_buys inválido'
  const fee = Number(fee_pct)
  if (!Number.isFinite(fee) || fee < 0) return 'fee_pct inválido'
  if (typeof trail_buy_pcts !== 'string' || !TRAIL_PCTS_RE.test(trail_buy_pcts.trim())) return 'trail_buy_pcts inválido'
  if (typeof trail_sell_pcts !== 'string' || !TRAIL_PCTS_RE.test(trail_sell_pcts.trim())) return 'trail_sell_pcts inválido'
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
              '--max-buys', String(Number(body.max_buys)),
              '--fee-pct', String(Number(body.fee_pct)),
              '--export-equity-json',
              '--trail-buy-pcts', body.trail_buy_pcts.trim(),
              '--trail-sell-pcts', body.trail_sell_pcts.trim(),
              '--out-dir', 'viewer/public/data',
            ]

            const child = spawn(VENV_PYTHON, args, { cwd: STRATEGY_DIR })
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
            const { run_ts, symbol } = body
            if (typeof run_ts !== 'string' || typeof symbol !== 'string') {
              res.statusCode = 400
              res.end(JSON.stringify({ error: 'run_ts y symbol son requeridos' }))
              return
            }

            const manifestPath = path.join(DATA_DIR, 'manifest.json')
            let manifest
            try {
              manifest = JSON.parse(await fs.readFile(manifestPath, 'utf-8'))
            } catch (err) {
              res.statusCode = 500
              res.end(JSON.stringify({ error: `No se pudo leer manifest.json: ${err.message}` }))
              return
            }

            const run = manifest.find((r) => r.run_ts === run_ts && r.symbol === symbol)
            if (!run) {
              res.statusCode = 404
              res.end(JSON.stringify({ error: 'run no encontrado en el manifest' }))
              return
            }

            const files = [run.file]
            for (const file of files) {
              const target = path.resolve(DATA_DIR, file)
              if (target !== path.normalize(target) || !target.startsWith(DATA_DIR + path.sep)) {
                res.statusCode = 400
                res.end(JSON.stringify({ error: `file fuera de la carpeta de datos: ${file}` }))
                return
              }
            }

            try {
              await Promise.all(files.map((file) => fs.unlink(path.resolve(DATA_DIR, file))))
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
