import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProcessingStages } from '../components/processing/ProcessingStages';
import { PIPELINE_STAGES, STAGE_STATE } from '../utils/constants';

describe('ProcessingStages', () => {
  const stages = PIPELINE_STAGES.map((stage, index) => ({
    ...stage,
    state: index === 0 ? STAGE_STATE.DONE : index === 1 ? STAGE_STATE.ACTIVE : STAGE_STATE.PENDING,
  }));

  it('renders every pipeline stage', () => {
    render(<ProcessingStages stages={stages} />);
    expect(screen.getByText('Upload recording')).toBeInTheDocument();
    expect(screen.getByText('Transcribe speech')).toBeInTheDocument();
    expect(screen.getByText('Analyse with AI')).toBeInTheDocument();
  });

  it('announces each stage state for screen readers', () => {
    render(<ProcessingStages stages={stages} />);
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('in progress')).toBeInTheDocument();
    expect(screen.getAllByText('not started').length).toBeGreaterThan(0);
  });

  it('marks a failed stage', () => {
    const failed = stages.map((stage, index) =>
      index === 3 ? { ...stage, state: STAGE_STATE.FAILED } : stage
    );
    render(<ProcessingStages stages={failed} />);
    expect(screen.getByText('failed')).toBeInTheDocument();
  });
});
