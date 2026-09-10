import { EmptyState } from '../ui';
import { FileText } from 'lucide-react';
import { toParagraphs } from '../../utils/format';

/** Reading view: the transcript as flowing paragraphs. */
export function ParagraphView({ transcript }) {
  const paragraphs = toParagraphs(transcript?.text);

  if (!paragraphs.length) {
    return (
      <EmptyState icon={FileText} title="No transcript text">
        This meeting has no transcript text to display.
      </EmptyState>
    );
  }

  return (
    <div className="transcript-paragraphs">
      {paragraphs.map((paragraph, index) => (
        // Paragraph order is stable for a stored transcript, so the index is a
        // safe key here.
        <p key={index}>{paragraph}</p>
      ))}
    </div>
  );
}
