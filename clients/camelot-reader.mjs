/** Read-only SEER v2 consumer. Validators and executable model routes are local-owned. */
import { createHash, randomUUID } from 'node:crypto'
import { lstat, mkdir, open, readFile, rename, unlink } from 'node:fs/promises'
import { resolve } from 'node:path'

const digest = bytes => createHash('sha256').update(bytes).digest('hex')
const LIMIT = 4 * 1024 * 1024
const instant = value => Date.parse(value)

async function bytesFrom(url, limit, signal) {
  const response = await fetch(url, { signal, redirect: 'error', credentials: 'omit' })
  if (!response.ok || response.body === null) throw new Error(`SEER HTTP ${response.status}`)
  const reader = response.body.getReader()
  let size = 0
  const parts = []
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > limit) throw new Error('SEER byte limit exceeded')
      parts.push(value)
    }
    return Buffer.concat(parts)
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

/** Exact inference selection; default queries never select an explicit high/max setting. */
export function matchVariant(table, local) {
  const matches = table.rows.filter(row => row.binding === 'matched' && row.provider === local.provider
    && row.apiModelId === local.apiModelId && row.inference.effort === (local.effort ?? 'unspecified')
    && row.inference.budget === (local.budget ?? null) && row.inference.harness === (local.harness ?? null)
    && (local.variantId === undefined || row.variantId === local.variantId))
  if (matches.length > 1) throw new Error('Ambiguous SEER inference variant')
  return matches[0] === undefined ? undefined : structuredClone(matches[0])
}

/** A verified evidence cache; never mounts tools, routes, credentials or model parameters. */
export class SeerEvidenceClient {
  constructor({ manifestUrl, cacheRoot, validateManifest, validateTable, timeoutMs = 4000 }) {
    this.url = new URL(manifestUrl)
    if (this.url.protocol !== 'https:' && !(this.url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(this.url.hostname))) {
      throw new Error('SEER requires HTTPS, except loopback tests')
    }
    if (this.url.username || this.url.password || this.url.hash) throw new Error('SEER publisher cannot carry credentials')
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000) throw new Error('SEER deadline must be 1..5000 ms')
    if (typeof validateManifest !== 'function' || typeof validateTable !== 'function') throw new Error('Local validators required')
    this.cacheRoot = cacheRoot
    this.validateManifest = validateManifest
    this.validateTable = validateTable
    this.timeoutMs = timeoutMs
    this.current = undefined
    this.lastError = null
  }

  admit(manifest, raw) {
    if (!this.validateManifest(manifest)) throw new Error('SEER manifest schema rejected')
    if (!/^snapshots\/[a-f0-9]{64}\.json$/.test(manifest.snapshot)) throw new Error('Unsafe SEER snapshot path')
    if (raw.byteLength > LIMIT || digest(raw) !== manifest.hash) throw new Error('SEER SHA-256 mismatch')
    const table = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw))
    if (!this.validateTable(table)) throw new Error('SEER table schema rejected')
    if (table.catalogVersion !== manifest.catalogVersion || table.generatedAt !== manifest.generatedAt || table.rows.length !== manifest.modelCount) {
      throw new Error('SEER table/manifest mismatch')
    }
    const sources = new Set(table.sources.map(source => source.id))
    if (sources.size !== table.sources.length || new Set(table.rows.map(row => row.variantId)).size !== table.rows.length) throw new Error('Duplicate SEER identity')
    for (const row of table.rows) {
      if (row.binding === 'matched' && (!row.canonicalId?.startsWith(row.provider + '/') || !row.apiModelId)) throw new Error('SEER model binding rejected')
      for (const score of Object.values(row.scores)) {
        if (!sources.has(score.sourceId) || score.lower > score.value || score.upper < score.value
          || score.metric === 'ips' && (score.value < -1 || score.value > 1)
          || score.metric === 'bradley-terry' && (score.value < 0 || score.value > 5000)) throw new Error('SEER evidence rejected')
      }
      if (row.facts) {
        for (const citation of [row.facts.provenance, ...row.facts.pricing.map(price => price.provenance)]) {
          if (citation.sourceIds.some(id => !sources.has(id))) throw new Error('SEER fact source rejected')
        }
      }
    }
    return table
  }

  async open() {
    if (!this.cacheRoot) return this
    try {
      const path = resolve(this.cacheRoot, 'seer-cache.json')
      const stat = await lstat(path)
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > LIMIT * 2) throw new Error('SEER cache file rejected')
      const cache = JSON.parse(await readFile(path, 'utf8'))
      if (cache.publisher !== digest(this.url.href)) throw new Error('SEER cache publisher mismatch')
      this.current = this.admit(cache.manifest, Buffer.from(cache.snapshot, 'utf8'))
    } catch (error) {
      if (error.code !== 'ENOENT') this.lastError = 'SEER cache rejected'
    }
    return this
  }

  async refresh() {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    let temporary
    try {
      const manifest = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await bytesFrom(this.url, 16384, controller.signal)))
      if (!this.validateManifest(manifest) || !/^snapshots\/[a-f0-9]{64}\.json$/.test(manifest.snapshot)) throw new Error('SEER manifest rejected')
      const url = new URL(manifest.snapshot, this.url)
      if (url.origin !== this.url.origin) throw new Error('SEER cross-origin snapshot')
      const raw = await bytesFrom(url, LIMIT, controller.signal)
      const table = this.admit(manifest, raw)
      if (this.current && (instant(table.generatedAt) < instant(this.current.generatedAt)
        || instant(table.generatedAt) === instant(this.current.generatedAt) && table.catalogVersion !== this.current.catalogVersion)) {
        throw new Error('SEER generation rollback rejected')
      }
      if (controller.signal.aborted) throw new Error('SEER deadline exceeded')
      if (this.cacheRoot) {
        await mkdir(this.cacheRoot, { recursive: true, mode: 0o700 })
        if ((await lstat(this.cacheRoot)).isSymbolicLink()) throw new Error('SEER cache root cannot be linked')
        temporary = resolve(this.cacheRoot, `seer-${randomUUID()}.tmp`)
        const handle = await open(temporary, 'wx', 0o600)
        try {
          await handle.writeFile(JSON.stringify({ publisher: digest(this.url.href), manifest, snapshot: raw.toString('utf8') }))
          await handle.sync()
        } finally { await handle.close() }
        if (controller.signal.aborted) throw new Error('SEER deadline exceeded')
        await rename(temporary, resolve(this.cacheRoot, 'seer-cache.json'))
      }
      this.current = table
      this.lastError = null
    } catch (error) { this.lastError = String(error.message).slice(0, 250) }
    finally { clearTimeout(timer); if (temporary) await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error }) }
    return this.current === undefined ? undefined : structuredClone(this.current)
  }

  match(local, now = Date.now()) {
    if (!this.current) return undefined
    const row = matchVariant(this.current, local)
    if (!row) return undefined
    for (const score of Object.values(row.scores)) {
      if (this.lastError !== null || now - instant(this.current.generatedAt) > 86400000 || now < instant(this.current.generatedAt)
        || now - instant(score.publishedAt) > 30 * 86400000 || now < instant(score.publishedAt)) score.freshness = 'stale'
    }
    return row
  }
}
