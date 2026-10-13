// Uses validators from the installed Camelot workspace and reads the actual v2 JSON.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'node:http'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { SeerEvidenceClient, matchVariant } from '../clients/camelot-reader.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const camelot = resolve(process.argv[2] || resolve(root, '../src'))
const require = createRequire(resolve(camelot, 'packages/camelot/provider-hub/package.json'))
const { default: Ajv } = require('ajv/dist/2020.js')
const ajv = new Ajv({ strict: false })
ajv.addFormat('date-time', value => /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)))
const validateTable = ajv.compile(JSON.parse(await readFile(resolve(root, 'schema/seer-table.schema.json'), 'utf8')))
const { registryManifestSchema } = await import(pathToFileURL(resolve(camelot, 'packages/camelot/provider-hub/src/registry-schema.ts')).href)
const validateManifest = value => registryManifestSchema.safeParse(value).success
let server
if (process.argv[3] === '--local') {
  server = createServer((request, response) => {
    const name = new URL(request.url, 'http://localhost').pathname.slice(1)
    if (!/^api\/v2\/(?:manifest\.json|snapshots\/[a-f0-9]{64}\.json)$/.test(name)) { response.writeHead(404).end(); return }
    readFile(resolve(root, 'public', name)).then(bytes => { response.setHeader('content-type', 'application/json'); response.end(bytes) }, () => response.writeHead(404).end())
  })
  await new Promise(done => server.listen(0, '127.0.0.1', done))
}
try {
const url = server ? `http://127.0.0.1:${server.address().port}/api/v2/manifest.json`
  : process.argv[3] || 'https://xiaoyu-msf519.github.io/Camelot-SEER/api/v2/manifest.json'
const options = { manifestUrl: url, cacheRoot: resolve(root, '.cache/camelot-table'), validateTable, validateManifest }
const client = await new SeerEvidenceClient(options).open()
const table = await client.refresh()
assert.equal(client.lastError, null, client.lastError)
assert(table.rows.length > 400)
const high = client.match({ provider: 'openai', apiModelId: 'gpt-5', effort: 'high' })
assert(high)
assert.equal(high.inference.effort, 'high')
assert(high.scores.coding)
assert.equal(matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5', effort: 'high', budget: '32k' }), undefined)
const ordinary = matchVariant(table, { provider: 'openai', apiModelId: 'gpt-5' })
assert(ordinary === undefined || ordinary.inference.effort === 'unspecified')
const reopened = await new SeerEvidenceClient(options).open()
assert.equal(reopened.current.catalogVersion, table.catalogVersion)
const bad = structuredClone(table)
bad.rows[0].apiKey = 'not-allowed'
assert.equal(validateTable(bad), false)
const report = { status: 'passed', catalogVersion: table.catalogVersion, variants: table.rows.length,
  matched: high.apiModelId, effort: high.inference.effort, coding: high.scores.coding.value,
  defaultDoesNotBorrowHigh: true, budgetExact: true, cacheReopened: true, executionFieldsRejected: true,
  reader: 'Camelot local Ajv + manifest validator; JSON v2 and read-only inference selection' }
await writeFile(resolve(root, '.cache/camelot-table-report.json'), JSON.stringify(report, null, 2) + '\n')
console.log(JSON.stringify(report, null, 2))
} finally { if (server) await new Promise(done => server.close(done)) }
