import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { History, MessageSquare, X } from "lucide-react";
import {
  engineHref,
  imagineKeys,
  putStudio,
} from "../../../api/endpoints/imagine";
import { refusalOf } from "../../../api/endpoints/create";
import { useStudio } from "../../../hooks/use-imagine";
import type { TwinNode } from "../../../types/twin";
import { HandoverPanel } from "./HandoverPanel";
import { SketchWorkspace } from "./SketchWorkspace";
import { StudioFrame } from "./StudioFrame";
import { describeChanges } from "./changes";

/** What the newest revision changed, against the one the person saw before it. */
interface StudioChange {
  revision: number;
  lines: string[];
  previous: Record<string, unknown>;
}

export type ImagineView = "studio" | "sketch";

const EMPTY: Record<string, unknown> = {};

interface ImagineStageProps {
  projectId: string;
  nodes: TwinNode[];
  view: ImagineView;
  onView: (view: ImagineView) => void;
  /** Opens the conversation with a request to start the studio. */
  onAskAssistant?: () => void;
  /** After handover: send a design turn in Develop (FORGE-824). */
  onStartDeveloping?: () => void;
  /** Puts a request in the conversation for the person to send (the studio's next step). */
  onAsk?: (text: string) => void;
}

/**
 * Imagine: intent and concept capture. The studio the assistant made for
 * this product, or the sketch paper, then the handover to engineering.
 * Checks here are screening only; Develop, Check and Make follow the twin.
 */
export function ImagineStage({
  projectId,
  nodes,
  view,
  onView,
  onAskAssistant,
  onStartDeveloping,
  onAsk,
}: ImagineStageProps) {
  const studio = useStudio(projectId);
  const data = studio.data;
  // The frame puts the revision in the address when a new one must load as a new page;
  // a change to the form alone is pushed into the running page instead.
  const src = data ? engineHref(data) : null;
  const hasStudio = !!data && data.status !== "none" && !!src;
  const qc = useQueryClient();

  // After each regeneration: what changed, and an undo that saves the revision before it again.
  const seen = useRef<{
    revision: number;
    config: Record<string, unknown>;
  } | null>(null);
  const [change, setChange] = useState<StudioChange | null>(null);
  const [undo, setUndo] = useState<{ busy: boolean; error: string | null }>({
    busy: false,
    error: null,
  });
  useEffect(() => {
    const rev = data?.revision;
    const cfg = data?.config;
    if (rev === null || rev === undefined || !cfg) return;
    const before = seen.current;
    if (before && rev > before.revision) {
      const lines = describeChanges(before.config, cfg);
      if (lines.length)
        setChange({ revision: rev, lines, previous: before.config });
    }
    if (!before || rev !== before.revision)
      seen.current = { revision: rev, config: cfg };
  }, [data?.revision, data?.config]);

  const undoChange = async () => {
    if (!change) return;
    setUndo({ busy: true, error: null });
    try {
      await putStudio(projectId, change.previous);
      setUndo({ busy: false, error: null });
      await qc.invalidateQueries({ queryKey: imagineKeys.studio(projectId) });
    } catch (err) {
      setUndo({
        busy: false,
        error: refusalOf(err) || "The change could not be undone.",
      });
    }
  };

  return (
    <div
      className={`cv-stage-col cv-imagine${view === "studio" && hasStudio ? " is-wide" : ""}`}
      data-testid="imagine-stage"
    >
      <div className="cv-seg" role="tablist" aria-label="Imagine">
        <button
          type="button"
          role="tab"
          aria-selected={view === "studio"}
          className={view === "studio" ? "is-on" : ""}
          onClick={() => onView("studio")}
        >
          Studio
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "sketch"}
          className={view === "sketch" ? "is-on" : ""}
          onClick={() => onView("sketch")}
        >
          Sketch
        </button>
      </div>

      {view === "sketch" ? (
        <SketchWorkspace projectId={projectId} nodes={nodes} />
      ) : studio.isLoading ? (
        <p className="cv-muted">Loading the studio…</p>
      ) : hasStudio ? (
        <>
          {change && (
            <section
              className="cv-studio-changes"
              data-testid="studio-changes"
              aria-live="polite"
            >
              <div className="cv-studio-changes-head">
                <History size={14} aria-hidden="true" />
                <strong>Changed in revision {change.revision}</strong>
                <span className="cv-studio-changes-actions">
                  {data.status !== "handed_over" && (
                    <button
                      type="button"
                      className="cv-chip-btn"
                      disabled={undo.busy}
                      onClick={() => void undoChange()}
                    >
                      {undo.busy ? "Undoing…" : "Undo this change"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="cv-icon-btn"
                    aria-label="Dismiss"
                    title="Dismiss"
                    onClick={() => setChange(null)}
                  >
                    <X size={14} />
                  </button>
                </span>
              </div>
              <ul>
                {change.lines.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
              {undo.error && (
                <p className="cv-error" role="alert">
                  {undo.error}
                </p>
              )}
            </section>
          )}
          <StudioFrame
            projectId={projectId}
            src={src}
            collections={data.collections ?? EMPTY}
            readOnly={data.status === "handed_over"}
            revision={data.revision ?? 0}
            config={data.config}
            onAsk={onAsk}
          />
        </>
      ) : (
        <div className="cv-empty" data-testid="studio-empty">
          <h2>No studio yet</h2>
          <p className="cv-muted">
            {studio.isError
              ? `The studio could not be loaded: ${refusalOf(studio.error)}`
              : "Ask the assistant to start the studio for this product. It captures what the product is for, the choices and the assumptions, ready for engineering."}
          </p>
          {onAskAssistant && !studio.isError && (
            <button
              type="button"
              className="cv-primary"
              onClick={onAskAssistant}
            >
              <MessageSquare size={14} aria-hidden="true" /> Ask the assistant
            </button>
          )}
        </div>
      )}

      {data && data.status !== "none" && (
        <HandoverPanel
          projectId={projectId}
          studio={data}
          onStartDeveloping={onStartDeveloping}
        />
      )}
    </div>
  );
}
