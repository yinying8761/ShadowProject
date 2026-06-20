import ReactMarkdown from 'react-markdown';

interface Props {
  text: string;
}

export function StreamingText({ text }: Props) {
  return (
    <div className="flex justify-start animate-fade-in">
      <div className="max-w-[80%] msg-assistant">
        <div className="text-sm leading-relaxed prose prose-invert prose-sm max-w-none">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
        <span className="inline-block w-2 h-4 bg-companion-accent ml-0.5 animate-cursor-blink align-text-bottom" />
      </div>
    </div>
  );
}
