const EXT = {
  go: 'go',
  js: 'javascript',
  cjs: 'javascript',
  mjs: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  hpp: 'cpp',
  java: 'java',
  rs: 'rust',
  vue: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  html: 'html',
  htm: 'html',
  json: 'json',
  md: 'markdown',
  sh: 'shell',
  bash: 'shell',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'ini',
  ini: 'ini',
  xml: 'xml',
  sql: 'sql',
  php: 'php',
  rb: 'ruby',
  kt: 'kotlin',
  swift: 'swift',
}

export function languageFor(path) {
  const name = path.toLowerCase()
  if (name.endsWith('dockerfile')) return 'dockerfile'
  const i = name.lastIndexOf('.')
  if (i === -1) return 'plaintext'
  return EXT[name.slice(i + 1)] || 'plaintext'
}
