import '../../styles/chat-blocks.css';
import type { UiBlock } from '../../types/ui-blocks';
import { Markdown } from './Markdown';
import { UiBlockView, type BlockActions } from './UiBlocks';

interface MessageBodyProps extends BlockActions {
  text: string;
  blocks?: UiBlock[] | null;
  /** Person's own messages render as typed: no Markdown, no blocks. */
  plain?: boolean;
}

/** One chat message: Markdown text, then any generative UI blocks the agent attached. */
export function MessageBody({ text, blocks, plain, ...actions }: MessageBodyProps) {
  if (plain) return <p className="mb-plain">{text}</p>;
  return (
    <div className="mb-body">
      {text.trim() && <Markdown text={text} />}
      {!!blocks?.length && (
        <div className="mb-blocks">
          {blocks.map((block, i) => (
            <UiBlockView key={`${block.type}-${i}`} block={block} {...actions} />
          ))}
        </div>
      )}
    </div>
  );
}
