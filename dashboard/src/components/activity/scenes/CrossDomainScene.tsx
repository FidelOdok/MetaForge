import { SceneFrame } from './SceneFrame';
import { layoutGraph } from './graph';
import { cls, type SceneGraph, type SceneProps } from './types';

/**
 * Cross-domain: connected items light up as dependent work is reached.
 *
 * With a `graph` (FORGE-793) the nodes and links are the project's real
 * interface edges, and the impact's own graph marks which items the change
 * reaches; an unconfirmed (proposed) link is drawn dashed. Without one, the
 * scene is the illustrative placeholder and is labelled as such.
 */
const NODES: [number, number][] = [
  [30, 50],
  [80, 25],
  [80, 75],
  [130, 50],
];
const LINKS = ['M30 50 L80 25', 'M30 50 L80 75', 'M80 25 L130 50', 'M80 75 L130 50'];

export function CrossDomainScene(props: SceneProps) {
  if (props.graph && props.graph.nodes.length >= 2) return <GraphScene props={props} graph={props.graph} />;
  return (
    <SceneFrame name="cross-domain" props={props}>
      {LINKS.map((d, i) => (
        <path
          key={d}
          className={cls('act-trace', i < props.stageIndex)}
          d={d}
          pathLength={100}
          style={{ animationDelay: `${i * 0.4}s` }}
        />
      ))}
      {NODES.map(([x, y], i) => (
        <circle
          key={`${x}-${y}`}
          className={cls('act-node', i < props.stageIndex)}
          cx={x}
          cy={y}
          r="7"
          style={{ animationDelay: `${i * 0.4}s` }}
        />
      ))}
    </SceneFrame>
  );
}

function GraphScene({ props, graph }: { props: SceneProps; graph: SceneGraph }) {
  const placed = layoutGraph(graph);
  const changed = graph.nodes.find((n) => n.role === 'changed');
  const reached = graph.nodes.filter((n) => n.role === 'impacted' || n.role === 'record').length;
  const label = changed
    ? `Dependency graph: ${changed.label} and ${reached} affected item${reached === 1 ? '' : 's'}`
    : `Dependency graph of ${graph.nodes.length} items`;
  return (
    <SceneFrame name="cross-domain" props={props} label={label}>
      {graph.links.map((link, i) => {
        const a = placed.get(link.source);
        const b = placed.get(link.target);
        if (!a || !b) return null;
        return (
          <path
            key={`${link.source}>${link.target}`}
            className={`${cls('act-trace', link.impacted)}${link.proposed ? ' is-proposed' : ''}`}
            data-link={`${link.source}>${link.target}`}
            data-impacted={link.impacted ? 'true' : 'false'}
            d={`M${a.x} ${a.y} L${b.x} ${b.y}`}
            pathLength={100}
            style={{ animationDelay: `${i * 0.3}s` }}
          />
        );
      })}
      {[...placed.values()].map((node, i) => (
        <circle
          key={node.id}
          className={`${cls('act-node', node.role === 'changed' || node.role === 'impacted' || node.role === 'record')} role-${node.role}`}
          data-node={node.id}
          data-role={node.role}
          cx={node.x}
          cy={node.y}
          r={node.role === 'changed' ? 8 : 6}
          style={{ animationDelay: `${i * 0.3}s` }}
        >
          <title>{`${node.label} (${node.domain})`}</title>
        </circle>
      ))}
    </SceneFrame>
  );
}
