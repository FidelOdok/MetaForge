import { useState } from 'react';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { useBom } from '../hooks/use-bom';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { ComponentChatPanel } from '../components/chat/integrations/ComponentChatPanel';
import type { BomComponent } from '../types/bom';

function BomRow({ component }: { component: BomComponent }) {
  const [chatOpen, setChatOpen] = useState(false);

  const chat = useScopedChat({
    scopeKind: 'bom-entry',
    entityId: component.id,
    defaultAgentCode: 'SC',
    label: component.partNumber,
  });

  return (
    <>
      <tr className="border-b border-zinc-100 dark:border-zinc-800">
        <td className="px-3 py-2 text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {component.designator}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300">
          {component.partNumber}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-600 dark:text-zinc-400">
          {component.description}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-600 dark:text-zinc-400">
          {component.manufacturer}
        </td>
        <td className="px-3 py-2 text-right text-sm text-zinc-700 dark:text-zinc-300">
          {component.quantity}
        </td>
        <td className="px-3 py-2 text-right text-sm text-zinc-700 dark:text-zinc-300">
          ${component.unitPrice.toFixed(2)}
        </td>
        <td className="px-3 py-2">
          <StatusBadge status={component.status} />
        </td>
        <td className="px-3 py-2">
          <button
            type="button"
            onClick={() => setChatOpen(!chatOpen)}
            className="text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            {chatOpen ? 'Hide' : 'Chat'}
          </button>
        </td>
      </tr>
      {chatOpen && (
        <tr>
          <td colSpan={8} className="px-3 py-2">
            <ComponentChatPanel
              componentId={component.id}
              componentName={component.partNumber}
              thread={chat.thread}
              messages={chat.messages}
              isTyping={chat.isTyping}
              onSendMessage={chat.sendMessage}
              onCreateThread={chat.createThread}
            />
          </td>
        </tr>
      )}
    </>
  );
}

export function BomPage() {
  const { data: components, isLoading } = useBom('proj-001');

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading BOM...</div>;
  }

  const items = components ?? [];
  const totalCost = items.reduce((sum, c) => sum + c.quantity * c.unitPrice, 0);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Bill of Materials
        </h2>
        <div className="text-sm text-zinc-500">
          {items.length} components &middot; Total: ${totalCost.toFixed(2)}
        </div>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="No components"
          description="BOM components will appear here when a project is loaded."
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/50">
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500">Ref</th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500">Part Number</th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500">Description</th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500">Manufacturer</th>
                <th className="px-3 py-2 text-right text-xs font-medium uppercase text-zinc-500">Qty</th>
                <th className="px-3 py-2 text-right text-xs font-medium uppercase text-zinc-500">Unit Price</th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500">Status</th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((component) => (
                <BomRow key={component.id} component={component} />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
