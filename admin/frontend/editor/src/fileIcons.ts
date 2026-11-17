import type { Component } from 'vue'
import File from '~icons/vscode-icons/default-file'
import Folder from '~icons/vscode-icons/default-folder'
import FolderOpen from '~icons/vscode-icons/default-folder-opened'
import C from '~icons/vscode-icons/file-type-c'
import Cpp from '~icons/vscode-icons/file-type-cpp'
import Css from '~icons/vscode-icons/file-type-css'
import Docker from '~icons/vscode-icons/file-type-docker'
import Git from '~icons/vscode-icons/file-type-git'
import Go from '~icons/vscode-icons/file-type-go'
import Html from '~icons/vscode-icons/file-type-html'
import Ini from '~icons/vscode-icons/file-type-ini'
import Java from '~icons/vscode-icons/file-type-java'
import Js from '~icons/vscode-icons/file-type-js-official'
import Json from '~icons/vscode-icons/file-type-json'
import Kotlin from '~icons/vscode-icons/file-type-kotlin'
import Less from '~icons/vscode-icons/file-type-less'
import Md from '~icons/vscode-icons/file-type-markdown'
import Php from '~icons/vscode-icons/file-type-php'
import Py from '~icons/vscode-icons/file-type-python'
import Reactjs from '~icons/vscode-icons/file-type-reactjs'
import Reactts from '~icons/vscode-icons/file-type-reactts'
import Ruby from '~icons/vscode-icons/file-type-ruby'
import Rs from '~icons/vscode-icons/file-type-rust'
import Scss from '~icons/vscode-icons/file-type-scss'
import Shell from '~icons/vscode-icons/file-type-shell'
import Sql from '~icons/vscode-icons/file-type-sql'
import Swift from '~icons/vscode-icons/file-type-swift'
import Text from '~icons/vscode-icons/file-type-text'
import Toml from '~icons/vscode-icons/file-type-toml'
import Ts from '~icons/vscode-icons/file-type-typescript-official'
import Vue from '~icons/vscode-icons/file-type-vue'
import Xml from '~icons/vscode-icons/file-type-xml'
import Yaml from '~icons/vscode-icons/file-type-yaml'

const byExtension: Record<string, Component> = {
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

const byName: Record<string, Component> = {
  dockerfile: Docker,
  '.gitignore': Git,
  '.gitattributes': Git,
  '.gitmodules': Git,
}

export const FolderIcon = Folder
export const FolderOpenIcon = FolderOpen
export const DefaultFileIcon = File

export const fileIcon = (name: string) => {
  const lower = name.toLowerCase()

  if (byName[lower]) return byName[lower]

  if (lower.endsWith('dockerfile')) return Docker

  const i = lower.lastIndexOf('.')

  return (i !== -1 && byExtension[lower.slice(i + 1)]) || File
}
