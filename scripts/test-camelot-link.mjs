// Real Camelot registry consumer against the deployed SEER feed; no model API calls.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const camelot = resolve(process.argv[2] || resolve(root, '../src'))
const url = process.argv[3] || 'https://xiaoyu-msf519.github.io/Camelot-SEER/api/camelot-v1/manifest.json'
const source = (name) => pathToFileURL(resolve(camelot, `packages/camelot/provider-hub/src/${name}.ts`)).href
const { ModelRegistry } = await import(source('registry'))
const { ProviderHub } = await import(source('provider-hub'))
const { snapshotDshRoutes } = await import(source('dsh-adapter'))
const seed = JSON.parse(await readFile(resolve(camelot, 'packages/camelot/provider-hub/data/seed-catalog.json'), 'utf8'))
const cacheRoot = resolve(root, '.cache/camelot-link')
await mkdir(cacheRoot, { recursive: true })

const registry = await ModelRegistry.open({ seed, url, cacheRoot, timeoutMs: 5000 })
try {
  const before = registry.status()
  const status = await registry.sync()
  assert.equal(status.lastError, null, `Remote sync failed: ${JSON.stringify(status)}`)
  assert.equal(status.origin, 'remote')
  const catalog = registry.snapshot()
  const identities = ['gpt-4o', 'gpt-4.1']
  for (const id of identities) {
    assert.equal(catalog.models[`openai/${id}`].identity.apiModelId, id)
  }
  assert.deepEqual(registry.requirements('coder'), { minContext: 8192, requireTools: true })

  // This local fixture models the dsh directory. It holds no real credential and
  // executes no model; registry JSON cannot create providers or admit tools.
  const directory = {
    listProviders: () => [{ id: 'openai', name: 'Local test adapter' }],
    listModels: async () => identities.map(id => ({ id })),
    resolveModelInfo: async () => ({ context: { contextWindow: 64000 }, defaultMaxTokens: 4096 }),
  }
  const makeSource = (tools, credentials) => ({ snapshot: snapshot => snapshotDshRoutes(snapshot,
    directory, undefined, [{ provider: 'openai', supportsTools: tools, ambientCredentialConfigured: credentials }]) })
  const hub = new ProviderHub(registry, makeSource(true, true))
  const selected = await hub.resolve({ profile: 'coder' })
  assert(identities.includes(selected.apiModelId))
  assert.equal(selected.contextWindow, 64000)
  await assert.rejects(new ProviderHub(registry, makeSource(false, true)).resolve({ profile: 'coder' }), /no eligible/)
  await assert.rejects(new ProviderHub(registry, makeSource(true, false)).resolve({ profile: 'coder' }), /no eligible/)
  await assert.rejects(hub.resolve({ profile: 'coder', requirements: { minContext: 128000 } }), /no eligible/)

  const reopened = await ModelRegistry.open({ seed, url, cacheRoot, timeoutMs: 5000 })
  try {
    assert.equal(reopened.status().origin, 'cache')
    assert.equal(reopened.snapshot().catalogVersion, catalog.catalogVersion)
    const controller = new AbortController()
    controller.abort()
    const offline = await reopened.sync(controller.signal)
    assert.equal(offline.lastError, 'cancelled')
    assert.equal(reopened.snapshot().catalogVersion, catalog.catalogVersion)
  } finally { await reopened.dispose() }

  const cache = JSON.parse(await readFile(resolve(cacheRoot, 'catalog-cache.json'), 'utf8'))
  assert.equal(createHash('sha256').update(cache.catalogText).digest('hex'), cache.manifest.hash)
  const report = { status: 'passed', url, before, after: status, modelCount: Object.keys(catalog.models).length,
    matchedLocalApiIds: identities, selectedLocalRoute: { provider: selected.provider, apiModelId: selected.apiModelId },
    localProfileFloorRetained: true, unknownToolAdapterDenied: true, missingCredentialDenied: true,
    localContextLimitEnforced: true, offlineCacheReopened: true, hashVerified: true,
    routingMode: 'real Camelot ProviderHub with an explicit local fixture directory; no real model calls' }
  await writeFile(resolve(root, '.cache/camelot-link-report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
} finally { await registry.dispose() }
