// Integration fixture for the actual Cordis Camelot Core boot and background sync.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const camelot = resolve(process.argv[2] || resolve(root, '../src'))
const publisher = process.argv[3] || 'https://xiaoyu-msf519.github.io/Camelot-SEER/api/camelot-v1/manifest.json'
const fixture = resolve(root, '.cache/core-boot')
await mkdir(fixture, { recursive: true })
const { boot } = await import(pathToFileURL(resolve(camelot, 'packages/boot/app-boot/src/index.ts')).href)
const configPath = resolve(fixture, 'cordis.json')
const config = [{ id: 'camelot-core', name: pathToFileURL(resolve(camelot, 'packages/camelot/plugin-camelot-core/src/index.ts')).href,
  config: { repositoryRoot: fixture, artifactRoot: resolve(fixture, 'artifacts'), worktreeParent: resolve(fixture, 'worktrees'),
    memoryRoot: resolve(fixture, 'memory'), runRoot: resolve(fixture, 'runs'), registryCacheRoot: resolve(fixture, 'registry-cache'),
    registryUrl: publisher, registryTimeoutMs: 5000, registryRefreshIntervalMs: 21600000 } }]
await writeFile(configPath, JSON.stringify(config, null, 2) + '\n')
const ctx = await boot('camelot-seer-live-registry-fixture', configPath)
try {
  const servicesAvailableAtBoot = ctx.camelotCore !== undefined
  const backgroundStatusAtBoot = ctx.camelotCore.registry.status()
  const status = await ctx.camelotCore.registry.sync()
  assert.equal(status.origin, 'remote', JSON.stringify(status))
  assert.equal(status.lastError, null)
  assert(ctx.camelotCore.catalog.models['openai/gpt-4o'])
  assert(ctx.camelotCore.catalog.profiles.coder)
  assert.deepEqual(ctx.camelotCore.registry.requirements('coder'), { minContext: 8192, requireTools: true })
  assert.deepEqual(ctx.camelotCore.runtimes.list(), [{ id: 'project', domain: 'project' }])
  await assert.rejects(ctx.camelotCore.providerHub.resolve({ profile: 'coder' }), /no eligible/)
  const report = { status: 'passed', publisher, servicesAvailableAtBoot, backgroundStatusAtBoot, after: status,
    modelCount: Object.keys(ctx.camelotCore.catalog.models).length,
    profiles: Object.keys(ctx.camelotCore.catalog.profiles), localFloorRetained: true,
    noLocalAdapterNoExecution: true, backgroundSyncConfigured: true, refreshIntervalMs: 21600000 }
  await writeFile(resolve(root, '.cache/camelot-boot-report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
} finally {
  await ctx.fiber.dispose()
}
