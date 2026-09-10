import { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import type { GatewayClient, Project, TwinNode, WorkProductRef } from "../api/client.js";
import { EmptyState } from "./EmptyState.js";

/** Digital-twin browser: projects → work products → node detail. */
export function TwinView({
  client,
  onExit,
}: {
  client: GatewayClient;
  onExit: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [pSel, setPSel] = useState(0);
  const [wSel, setWSel] = useState(0);
  const [level, setLevel] = useState<"projects" | "products">("projects");
  const [node, setNode] = useState<TwinNode | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void client
      .listProjects()
      .then((p) => alive && setProjects(p))
      .catch((e: Error) => alive && setErr(e.message))
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, [client]);

  const project = projects[pSel];
  const products: WorkProductRef[] = project?.work_products ?? [];
  const product = products[wSel];

  // Fetch node detail for the selected work product.
  useEffect(() => {
    setNode(null);
    if (level !== "products" || !product) return;
    let alive = true;
    void client
      .getNode(product.id)
      .then((n) => alive && setNode(n))
      .catch(() => alive && setNode(null));
    return () => {
      alive = false;
    };
  }, [client, level, product?.id]);

  useInput((_input, key) => {
    if (key.escape) {
      if (level === "products") setLevel("projects");
      else onExit();
      return;
    }
    if (level === "projects") {
      if (key.upArrow) setPSel((s) => Math.max(0, s - 1));
      if (key.downArrow) setPSel((s) => Math.min(Math.max(0, projects.length - 1), s + 1));
      if (key.return && project) {
        setWSel(0);
        setLevel("products");
      }
    } else {
      if (key.upArrow) setWSel((s) => Math.max(0, s - 1));
      if (key.downArrow) setWSel((s) => Math.min(Math.max(0, products.length - 1), s + 1));
    }
  });

  const props = node?.properties ?? {};
  const meta = node?.metadata ?? (props as Record<string, unknown>);
  const fmt = (props.format ?? props.wp_type ?? "") as string;
  const hash = (node?.content_hash ?? (props.content_hash as string) ?? "") as string;
  const loadable = Boolean(meta.minio_object_key ?? props.minio_object_key ?? hash);

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>
        Digital Twin <Text dimColor>· ↑/↓ · Enter open · Esc back</Text>
      </Text>
      {err && <Text color="red">  error: {err}</Text>}

      <Box marginTop={1} flexDirection="column">
        <Text bold color={level === "projects" ? "cyan" : undefined}>Projects</Text>
        {!loaded && projects.length === 0 && !err ? <Text dimColor>  loading…</Text> : null}
        {loaded && projects.length === 0 && !err ? (
          <EmptyState
            title="No projects yet."
            hints={["Start a design in chat (Ctrl+T), and projects show up here"]}
          />
        ) : null}
        {projects.slice(0, 6).map((p, i) => (
          <Text key={p.id}>
            <Text color={level === "projects" && i === pSel ? "cyan" : undefined}>
              {level === "projects" && i === pSel ? "  ❯ " : "    "}
            </Text>
            {p.name} <Text dimColor>({p.work_products?.length ?? 0})</Text>
          </Text>
        ))}
      </Box>

      {level === "products" && project && (
        <Box marginTop={1} flexDirection="column">
          <Text bold color="cyan">
            {project.name} — work products
          </Text>
          {products.length === 0 ? (
            <EmptyState
              title="No work products yet."
              hints={["This project has no reviewable artifacts in the twin"]}
            />
          ) : null}
          {products.slice(0, 8).map((w, i) => (
            <Text key={w.id}>
              <Text color={i === wSel ? "cyan" : undefined}>{i === wSel ? "  ❯ " : "    "}</Text>
              {w.name} <Text dimColor>[{w.type}]</Text>
            </Text>
          ))}

          {product && (
            <Box
              marginTop={1}
              flexDirection="column"
              borderStyle="round"
              borderColor="gray"
              paddingX={1}
            >
              <Text>
                <Text dimColor>name </Text>
                {product.name}
              </Text>
              <Text>
                <Text dimColor>type </Text>
                {product.type}
                {fmt ? <Text dimColor> · {fmt}</Text> : null}
              </Text>
              {hash ? (
                <Text>
                  <Text dimColor>hash </Text>
                  {hash.slice(0, 16)}
                </Text>
              ) : null}
              {product.type === "cad_model" && (
                <Text color={loadable ? "green" : "yellow"}>
                  {loadable ? "● viewable" : "○ no blob"} —{" "}
                  <Text dimColor>/v1/twin/nodes/{product.id.slice(0, 8)}…/model</Text>
                </Text>
              )}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}
