// Optional integration check against the existing read-only Camelot checkout.
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const camelotRoot = resolve(process.argv[2] || resolve(root, '../src'))
const directory = resolve(root, 'dist/api/camelot-v1')
const manifest = JSON.parse(readFileSync(resolve(directory, 'manifest.json'), 'utf8'))
const raw = readFileSync(resolve(directory, manifest.snapshot))
if (createHash('sha256').update(raw).digest('hex') !== manifest.hash) throw new Error('SHA-256 mismatch')
const { validateCatalog } = await import(pathToFileURL(resolve(camelotRoot, 'packages/camelot/provider-hub/src/schema.ts')).href)
const catalog = validateCatalog(JSON.parse(raw.toString('utf8')))
console.log(`Camelot validateCatalog passed: ${Object.keys(catalog.models).length} models, read-only upstream verification`)
