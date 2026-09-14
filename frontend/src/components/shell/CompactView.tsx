import { CharacterDisplay } from '../character/CharacterDisplay';
import { DialogueBox } from '../chat/DialogueBox';
import { InputBar } from '../chat/InputBar';
import { ToolStatusStrip } from '../chat/ToolStatusStrip';
import { ErrorBubble } from '../chat/ErrorBubble';
import { RetryIndicator } from '../chat/RetryIndicator';

/**
 * Compact mode: portrait C位 + 最新对话 + 输入
 * The classic "desktop companion" layout.
 */
export function CompactView() {
  return (
    <>
      {/* Portrait fills remaining vertical space */}
      <CharacterDisplay />

      {/* Bottom dialogue + input stack */}
      <div className="flex-shrink-0 pb-1">
        <DialogueBox />
        <ErrorBubble />
        <RetryIndicator />
        <ToolStatusStrip />
        <InputBar />
      </div>
    </>
  );
}
