import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { readFile, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import { SeerEvidenceClient, matchVariant } from '../camelot-reader.mjs'

const directory = resolve('public/api/v2')
const manifest = JSON.parse(await readFile(resolve(directory, 'manifest.json'), 'utf8'))
const raw = await readFile(resolve(directory, manifest.snapshot))
const table = JSON.parse(raw.toString('utf8'))
// Transport tests inject a small trusted validator fixture. The integration runner
// separately compiles the complete local JSON Schema with Camelot's real Ajv.
const validators = {
  validateManifest: value => value.schemaVersion === 1 && typeof value.hash === 'string' && Number.isInteger(value.modelCount),
  validateTable: value => value.schemaVersion === 2 && value.policyVersion === 'arena-variants-1' && Array.isArray(value.rows),
}

test('Node evidence reader verifies HTTP data and recovers cache after failure', async () => {
  const cacheRoot = await mkdtemp(join(tmpdir(), 'seer-client-'))
  let mode = 'ok'
  const server = createServer((request, response) => {
    if (mode === 'offline') { response.writeHead(503).end(); return }
    response.setHeader('content-type', 'application/json')
    response.end(request.url.endsWith('manifest.json') ? JSON.stringify(manifest) : mode === 'corrupt' ? Buffer.concat([raw, Buffer.from(' ')]) : raw)
  })
  await new Promise(done => server.listen(0, '127.0.0.1', done))
  try {
    const manifestUrl = `http://127.0.0.1:${server.address().port}/manifest.json`
    const options = { manifestUrl, cacheRoot, ...validators }
    const client = await new SeerEvidenceClient(options).open()
    assert((await client.refresh()).rows.length > 400)
    assert.equal(client.lastError, null)
    mode = 'corrupt'
    const kept = await client.refresh()
    assert.equal(kept.catalogVersion, table.catalogVersion)
    assert.match(client.lastError, /SHA-256/)
    const reopened = await new SeerEvidenceClient(options).open()
    assert.equal(reopened.current.catalogVersion, table.catalogVersion)
    mode = 'offline'
    await reopened.refresh()
    assert.match(reopened.lastError, /503/)
    const different = await new SeerEvidenceClient({ ...options, manifestUrl: manifestUrl + '?different' }).open()
    assert.equal(different.current, undefined)
  } finally {
    await new Promise(done => server.close(done))
    await rm(cacheRoot, { recursive: true, force: true })
  }
})

test('Node matching requires the exact effort, budget and harness', () => {
  const high = matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5', effort: 'high' })
  assert(high)
  assert.equal(high.inference.effort, 'high')
  const ordinary = matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5' })
  assert(ordinary === undefined || ordinary.inference.effort === 'unspecified')
  assert.equal(matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5', effort: 'high', budget: '32k' }), undefined)
  assert.equal(matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5', effort: 'high', harness: 'unadmitted' }), undefined)
})

test('Node reader rejects bad publishers and long deadlines', () => {
  assert.throws(() => new SeerEvidenceClient({ manifestUrl: 'file:///tmp/table.json', ...validators }), /HTTPS/)
  assert.throws(() => new SeerEvidenceClient({ manifestUrl: 'https://example.test/manifest.json', timeoutMs: 6000, ...validators }), /deadline/)
})
