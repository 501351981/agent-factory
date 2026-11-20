import type { BoardArtifactDto } from './api'

export type BoardTreeFile = {
  kind: 'file'
  id: string
  name: string
  path: string
  artifact: BoardArtifactDto
}

export type BoardTreeFolder = {
  kind: 'folder'
  id: string
  name: string
  path: string
  children: BoardTreeNode[]
}

export type BoardTreeNode = BoardTreeFolder | BoardTreeFile

export function shortPathName(path: string): string {
  const parts = (path || '').split('/').filter(Boolean)
  return parts[parts.length - 1] || path || '未命名'
}

/** Build a folder tree from board artifact paths (`a/b/c.md`). */
export function buildArtifactTree(artifacts: BoardArtifactDto[]): BoardTreeNode[] {
  type MutableFolder = {
    kind: 'folder'
    id: string
    name: string
    path: string
    children: BoardTreeNode[]
    folders: Map<string, MutableFolder>
  }

  const root: MutableFolder = {
    kind: 'folder',
    id: '',
    name: '',
    path: '',
    children: [],
    folders: new Map(),
  }

  const sorted = [...(artifacts || [])].sort((a, b) => a.path.localeCompare(b.path))
  for (const art of sorted) {
    const parts = (art.path || '').split('/').filter(Boolean)
    if (!parts.length) {
      root.children.push({
        kind: 'file',
        id: art.id,
        name: art.title || '未命名',
        path: art.path || art.id,
        artifact: art,
      })
      continue
    }
    let cursor = root
    for (let i = 0; i < parts.length - 1; i += 1) {
      const seg = parts[i]
      const folderPath = parts.slice(0, i + 1).join('/')
      let next = cursor.folders.get(seg)
      if (!next) {
        next = {
          kind: 'folder',
          id: `dir:${folderPath}`,
          name: seg,
          path: folderPath,
          children: [],
          folders: new Map(),
        }
        cursor.folders.set(seg, next)
        cursor.children.push(next)
      }
      cursor = next
    }
    const fileName = parts[parts.length - 1]
    cursor.children.push({
      kind: 'file',
      id: art.id,
      name: art.title?.trim() || fileName,
      path: art.path,
      artifact: art,
    })
  }

  const strip = (node: MutableFolder): BoardTreeFolder => ({
    kind: 'folder',
    id: node.id,
    name: node.name,
    path: node.path,
    children: node.children.map((child) =>
      child.kind === 'folder' ? strip(child as MutableFolder) : child,
    ),
  })

  return strip(root).children
}
