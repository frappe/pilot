import Go from '~icons/vscode-icons/file-type-go'
import Js from '~icons/vscode-icons/file-type-js-official'
import Ts from '~icons/vscode-icons/file-type-typescript-official'
import Reactjs from '~icons/vscode-icons/file-type-reactjs'
import Reactts from '~icons/vscode-icons/file-type-reactts'
import Py from '~icons/vscode-icons/file-type-python'
import Rs from '~icons/vscode-icons/file-type-rust'
import Java from '~icons/vscode-icons/file-type-java'
import C from '~icons/vscode-icons/file-type-c'
import Cpp from '~icons/vscode-icons/file-type-cpp'
import Html from '~icons/vscode-icons/file-type-html'
import Css from '~icons/vscode-icons/file-type-css'
import Scss from '~icons/vscode-icons/file-type-scss'
import Less from '~icons/vscode-icons/file-type-less'
import Vue from '~icons/vscode-icons/file-type-vue'
import Json from '~icons/vscode-icons/file-type-json'
import Md from '~icons/vscode-icons/file-type-markdown'
import Yaml from '~icons/vscode-icons/file-type-yaml'
import Toml from '~icons/vscode-icons/file-type-toml'
import Xml from '~icons/vscode-icons/file-type-xml'
import Sql from '~icons/vscode-icons/file-type-sql'
import Shell from '~icons/vscode-icons/file-type-shell'
import Php from '~icons/vscode-icons/file-type-php'
import Ruby from '~icons/vscode-icons/file-type-ruby'
import Kotlin from '~icons/vscode-icons/file-type-kotlin'
import Swift from '~icons/vscode-icons/file-type-swift'
import Docker from '~icons/vscode-icons/file-type-docker'
import Ini from '~icons/vscode-icons/file-type-ini'
import Git from '~icons/vscode-icons/file-type-git'
import Text from '~icons/vscode-icons/file-type-text'
import Folder from '~icons/vscode-icons/default-folder'
import FolderOpen from '~icons/vscode-icons/default-folder-opened'
import File from '~icons/vscode-icons/default-file'

const BY_EXT = {
  go: Go,
  js: Js,
  cjs: Js,
  mjs: Js,
  jsx: Reactjs,
  ts: Ts,
  tsx: Reactts,
  py: Py,
  rs: Rs,
  java: Java,
  c: C,
  h: C,
  cpp: Cpp,
  cc: Cpp,
  cxx: Cpp,
  hpp: Cpp,
  html: Html,
  htm: Html,
  css: Css,
  scss: Scss,
  less: Less,
  vue: Vue,
  json: Json,
  md: Md,
  markdown: Md,
  yml: Yaml,
  yaml: Yaml,
  toml: Toml,
  xml: Xml,
  sql: Sql,
  sh: Shell,
  bash: Shell,
  zsh: Shell,
  php: Php,
  rb: Ruby,
  kt: Kotlin,
  swift: Swift,
  ini: Ini,
  cfg: Ini,
  conf: Ini,
  txt: Text,
}

const BY_NAME = {
  dockerfile: Docker,
  '.gitignore': Git,
  '.gitattributes': Git,
  '.gitmodules': Git,
}

export const FolderIcon = Folder
export const FolderOpenIcon = FolderOpen
export const DefaultFileIcon = File

export function fileIcon(name) {
  const lower = name.toLowerCase()
  if (BY_NAME[lower]) return BY_NAME[lower]
  if (lower.endsWith('dockerfile')) return Docker
  const i = lower.lastIndexOf('.')
  if (i !== -1) {
    const ext = lower.slice(i + 1)
    if (BY_EXT[ext]) return BY_EXT[ext]
  }
  return File
}
