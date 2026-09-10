import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ParagraphView } from '../components/transcript/ParagraphView';
import { TimelineView } from '../components/transcript/TimelineView';

const segments = [
  { segment_index: 0, start_time: 0, end_time: 8, text: 'Welcome everyone.', speaker: null },
  { segment_index: 1, start_time: 9, end_time: 22, text: 'Ravi will handle the API integration.', speaker: 'Ravi' },
];

describe('ParagraphView', () => {
  it('renders each paragraph of the transcript', () => {
    render(<ParagraphView transcript={{ text: 'First paragraph.\n\nSecond paragraph.' }} />);
    expect(screen.getByText('First paragraph.')).toBeInTheDocument();
    expect(screen.getByText('Second paragraph.')).toBeInTheDocument();
  });

  it('shows an empty state when there is no text', () => {
    render(<ParagraphView transcript={{ text: '' }} />);
    expect(screen.getByText('No transcript text')).toBeInTheDocument();
  });
});

describe('TimelineView', () => {
  it('renders timestamps and text for each segment', () => {
    render(<TimelineView segments={segments} />);
    expect(screen.getByText('Welcome everyone.')).toBeInTheDocument();
    expect(screen.getByText('00:00')).toBeInTheDocument();
    expect(screen.getByText('00:22')).toBeInTheDocument();
  });

  it('shows the speaker label when the backend provides one', () => {
    render(<TimelineView segments={segments} />);
    expect(screen.getByText('Ravi:')).toBeInTheDocument();
  });

  it('explains the empty case instead of rendering nothing', () => {
    render(<TimelineView segments={[]} />);
    expect(screen.getByText('No timed segments')).toBeInTheDocument();
  });
});
