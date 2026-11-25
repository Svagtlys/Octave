import { useNavigate } from '@tanstack/react-router';
import { ConversationList } from '../components/chat/ConversationList';
import { useConversations } from '../lib/api/useConversations';

/**
 * Session hub (issue #46): the /sessions landing surface — the same
 * ConversationList the chat view mounts as a panel, standalone and with no
 * active selection. New chat navigates into the created session.
 */
export default function SessionsHubView() {
  const navigate = useNavigate();
  const conversations = useConversations();

  return (
    <div>
      <h2>Sessions</h2>
      <ConversationList
        conversations={conversations}
        activeId={null}
        onSelect={(id) => void navigate({ to: '/sessions/$sessionId', params: { sessionId: id } })}
        onNew={() => {
          void conversations.create().then((id) => {
            if (id) void navigate({ to: '/sessions/$sessionId', params: { sessionId: id } });
          });
        }}
        onConfirmDelete={(id) => {
          void conversations.remove(id).catch(() => undefined);
        }}
      />
    </div>
  );
}
