import { useEffect, useRef, useState } from 'react';
import URDFLoader, { type URDFRobot } from 'urdf-loader';

/**
 * MET-737: load an exported URDF (text) + its meshes into a live
 * `URDFRobot` (a `THREE.Object3D` with `.links`/`.joints`, each joint
 * exposing `setJointValue`/`limit` — the kinematic-posing API this preview
 * is built on).
 *
 * All mesh files from one export share a single `{export_id}` directory
 * (`api_gateway/cad_export/routes.py::_export_file` derives every
 * `download_url` — the URDF's own and every mesh's — from the same
 * `export_id`), so `meshBaseUrl` (that shared directory's URL) is enough
 * for `URDFLoader.workingPath` to resolve every bare `<mesh filename=...>`
 * reference URDF export always writes (no `package://` prefix) — no
 * per-file filename→URL map needed.
 *
 * Mesh format is always STL here (the caller forces `mesh_format: 'stl'`
 * for the preview's own export call, regardless of what the user picked
 * for their actual download) — `urdf-loader`'s default mesh loader already
 * handles STL via three.js's own `STLLoader`, so no custom `loadMeshCb`.
 */
export function useUrdfRobot(urdfText: string | null, meshBaseUrl: string) {
  const [robot, setRobot] = useState<URDFRobot | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const loaderRef = useRef<URDFLoader | null>(null);

  useEffect(() => {
    if (!urdfText) {
      setRobot(null);
      setError(null);
      return;
    }

    const loader = new URDFLoader();
    loader.workingPath = meshBaseUrl;
    loaderRef.current = loader;

    let cancelled = false;
    try {
      const parsed = loader.parse(urdfText);
      if (!cancelled) {
        setRobot(parsed);
        setError(null);
      }
    } catch (err) {
      if (!cancelled) {
        setError(err instanceof Error ? err : new Error(String(err)));
        setRobot(null);
      }
    }

    return () => {
      cancelled = true;
    };
  }, [urdfText, meshBaseUrl]);

  return { robot, error };
}

/** Derive the shared mesh directory URL a URDF's own file URL lives
 * alongside, for either of the two shapes this preview can be fed from:
 *
 * - A fresh export: `ExportFile.download_url`s all share a
 *   `/v1/cad-export/download/{export_id}/` prefix.
 * - A persisted `robot_description` Twin node (MET-740 follow-up): its
 *   URDF is `/v1/twin/nodes/{id}/file`, and its mesh files live at the
 *   sibling `/v1/twin/nodes/{id}/files/{filename}` route — letting the
 *   preview point straight at a saved node with no re-export round trip.
 *
 * Returns '' if the URL matches neither shape rather than guessing, so a
 * resolver mismatch fails loudly (a broken preview) instead of silently
 * loading nothing. */
export function meshBaseUrlFrom(downloadUrl: string): string {
  const exportMatch = downloadUrl.match(/^(.*\/download\/[^/]+\/)[^/]+$/);
  if (exportMatch?.[1]) return exportMatch[1];
  const nodeMatch = downloadUrl.match(/^(.*\/nodes\/[^/]+)\/file$/);
  if (nodeMatch?.[1]) return `${nodeMatch[1]}/files/`;
  return '';
}
