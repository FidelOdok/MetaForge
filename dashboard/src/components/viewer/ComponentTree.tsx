import { useState, useMemo } from 'react';
import { ChevronRight, ChevronDown, Eye, EyeOff, Search, ChevronsUpDown } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';
import type { PartTreeNode } from '../../types/viewer';

interface TreeNodeProps {
  node: PartTreeNode;
  depth: number;
  searchTerm: string;
}

function TreeNodeItem({ node, depth, searchTerm }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(true);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const hiddenMeshes = useViewerStore((s) => s.hiddenMeshes);
  const selectPart = useViewerStore((s) => s.selectPart);
  const toggleVisibility = useViewerStore((s) => s.toggleVisibility);

  const isSelected = selectedMeshName === node.meshName;
  const isHidden = hiddenMeshes.has(node.meshName);
  const hasChildren = node.children.length > 0;

  const matchesSearch =
    !searchTerm || node.name.toLowerCase().includes(searchTerm.toLowerCase());

  const childMatches = useMemo(() => {
    if (!searchTerm) return true;
    const check = (n: PartTreeNode): boolean =>
      n.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      n.children.some(check);
    return node.children.some(check);
  }, [node, searchTerm]);

  if (!matchesSearch && !childMatches) return null;

  return (
    <div>
      <div
        className={`flex items-center gap-1 py-1 pr-2 text-xs transition-colors cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800 ${
          isSelected ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' : 'text-zinc-700 dark:text-zinc-300'
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {/* Expand/collapse */}
        {hasChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
            className="flex-shrink-0 p-0.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
        ) : (
          <span className="w-4 flex-shrink-0" />
        )}

        {/* Part name */}
        <button
          type="button"
          onClick={() => selectPart(node.meshName)}
          className="min-w-0 flex-1 truncate text-left"
        >
          {node.name}
        </button>

        {/* Visibility toggle */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            toggleVisibility(node.meshName);
          }}
          className="flex-shrink-0 p-0.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
          title={isHidden ? 'Show' : 'Hide'}
        >
          {isHidden ? <EyeOff size={12} /> : <Eye size={12} />}
        </button>
      </div>

      {/* Children */}
      {expanded &&
        hasChildren &&
        node.children.map((child) => (
          <TreeNodeItem
            key={child.meshName}
            node={child}
            depth={depth + 1}
            searchTerm={searchTerm}
          />
        ))}
    </div>
  );
}

export function ComponentTree() {
  const manifest = useViewerStore((s) => s.manifest);
  const [searchTerm, setSearchTerm] = useState('');
  const [allExpanded, setAllExpanded] = useState(true);

  if (!manifest) return null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-zinc-200 p-3 dark:border-zinc-700">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Components
          </h3>
          <button
            type="button"
            onClick={() => setAllExpanded(!allExpanded)}
            className="p-0.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
            title={allExpanded ? 'Collapse all' : 'Expand all'}
          >
            <ChevronsUpDown size={14} />
          </button>
        </div>

        {/* Search */}
        <div className="relative mt-2">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-400" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search parts..."
            className="w-full rounded border border-zinc-200 bg-white py-1 pl-6 pr-2 text-xs text-zinc-700 placeholder:text-zinc-400 focus:border-blue-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {manifest.parts.map((part) => (
          <TreeNodeItem
            key={part.meshName}
            node={part}
            depth={0}
            searchTerm={searchTerm}
          />
        ))}
      </div>

      {/* Stats footer */}
      <div className="border-t border-zinc-200 px-3 py-2 text-[10px] text-zinc-400 dark:border-zinc-700">
        {manifest.stats.triangleCount.toLocaleString()} triangles &middot;{' '}
        {(manifest.stats.fileSize / 1024).toFixed(1)} KB
      </div>
    </div>
  );
}
