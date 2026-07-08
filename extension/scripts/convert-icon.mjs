import sharp from 'sharp'
import { readFileSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const svgPath = join(__dirname, '..', 'public', 'favicon.svg')
const outputDir = join(__dirname, '..', 'public')

const svgBuffer = readFileSync(svgPath)

const sizes = [16, 48, 128]

for (const size of sizes) {
  await sharp(svgBuffer, { density: 300 })
    .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(join(outputDir, `icon${size}.png`))

  console.log(`Generated icon${size}.png`)
}
